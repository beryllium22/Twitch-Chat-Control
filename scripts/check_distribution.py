"""Check this application's source and packaging credential boundary.

Scope: shipped application Python and public documentation, tracked forbidden
filenames, the PyInstaller source/data allowlist, and optionally a built directory.
Checks detect common literal credentials, private-key material and accidentally
included user files. Untracked local files outside explicit build inputs are not
scanned or included. Third-party executable/bytecode archives aren't unpacked.

This is a bounded regression gate, not proof that arbitrary code or a binary is
secret-free. It doesn't perform entropy analysis or replace a security review.
Diagnostics contain filenames and rule names, never matched credential values.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

APP_SOURCE = "src/twitch_chat_control"
ENTRYPOINT = "packaging/run_app.py"
SPEC_PATH = "packaging/TwitchChatControl.spec"
PUBLIC_DOCS = frozenset(
    {
        "README.md",
        "docs/SECURITY.md",
        "docs/TWITCH_SETUP.md",
        "docs/PROFILES.md",
        "docs/VALIDATION.md",
    }
)
MAX_TEXT_BYTES = 2_000_000
_SECRET_NAMES = frozenset(
    {
        "access",
        "refresh",
        "token",
        "secret",
        "password",
        "passwd",
        "credentials",
        "access_token",
        "refresh_token",
        "oauth_token",
        "auth_token",
        "client_secret",
        "client_id",
        "api_key",
        "device_code",
        "user_code",
    }
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
_TOKEN_PATTERNS = (
    re.compile(r"\boauth:[A-Za-z0-9_-]{20,}\b", re.IGNORECASE),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
)
_DOC_ASSIGNMENT = re.compile(
    r"""["'](?:access_token|refresh_token|client_secret|client_id|api_key|password)["']\s*[:=]\s*["'][A-Za-z0-9._~+/=-]{12,}["']""",
    re.IGNORECASE,
)
_TEXT_SUFFIXES = frozenset({".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".pem"})


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    rule: str


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _forbidden_filename(path: str, *, distribution: bool = False) -> bool:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    name = parts[-1].lower() if parts else ""
    lower_parts = {part.lower() for part in parts}
    if name == ".env" or name.startswith(".env.") or name.endswith((".key", ".p12", ".pfx", ".log")):
        return True
    if name in {"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "authorized_keys"}:
        return True
    if re.fullmatch(
        r"(?:credentials?|secrets?|tokens?|local[-_]settings)(?:[-_.].*)?", name
    ) and PurePosixPath(name).suffix not in {".py", ".pyc", ".pyd", ".dll", ".so"}:
        return True
    if distribution:
        if lower_parts & {".git", ".venv", "__pycache__", "tests", "profiles", "user_profiles"}:
            return True
        if name in {"settings.json", "config.json", "user-settings.json", "user_config.json"}:
            return True
        if re.fullmatch(r"(?:user[-_])?profiles?(?:[-_].+)?\.json", name):
            return True
    return False


def _read_text(path: Path, root: Path, findings: list[Finding]) -> str | None:
    display = _display_path(path, root)
    try:
        if not path.resolve().is_relative_to(root.resolve()):
            findings.append(Finding(display, "build_input_escapes_root"))
            return None
        raw = path.read_bytes()
        if len(raw) > MAX_TEXT_BYTES:
            findings.append(Finding(display, "text_input_exceeds_scan_limit"))
            return None
        return raw.decode("utf-8")
    except (OSError, UnicodeError):
        findings.append(Finding(display, "text_input_unreadable"))
        return None


def _content_checks(text: str, display: str, findings: list[Finding], *, docs: bool) -> None:
    if _PRIVATE_KEY.search(text):
        findings.append(Finding(display, "private_key_material"))
    if any(pattern.search(text) for pattern in _TOKEN_PATTERNS):
        findings.append(Finding(display, "recognizable_token_literal"))
    if docs and _DOC_ASSIGNMENT.search(text):
        findings.append(Finding(display, "credential_literal_in_text"))


def _sensitive(name: str) -> bool:
    name = name.strip("_").lower().replace("-", "_")
    for prefix in ("default_", "twitch_", "oauth_"):
        name = name.removeprefix(prefix)
    for suffix in ("_default", "_var", "_value"):
        name = name.removesuffix(suffix)
    return name in _SECRET_NAMES


def _literal_string(node: ast.AST | None) -> str | bytes | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_string(node.left), _literal_string(node.right)
        if left is not None and right is not None and type(left) is type(right):
            return left + right
    if isinstance(node, ast.JoinedStr) and all(isinstance(value, ast.Constant) for value in node.values):
        return "".join(str(value.value) for value in node.values)
    return None


def _target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return node.slice.value
    return ""


def _credential_ast_checks(tree: ast.AST, display: str, findings: list[Finding]) -> None:
    def check(name: str, value: ast.AST | None) -> None:
        if not _sensitive(name):
            return
        literal = _literal_string(value)
        if literal:
            findings.append(Finding(display, "nonempty_credential_or_client_id_literal"))
        if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
            for fallback in value.values[1:]:
                if _literal_string(fallback):
                    findings.append(Finding(display, "nonempty_credential_or_client_id_literal"))
        if isinstance(value, ast.Call):
            # Catch a prefilled Client ID/password StringVar or similar wrapper.
            for keyword in value.keywords:
                if keyword.arg in {"value", "default"} and _literal_string(keyword.value):
                    findings.append(Finding(display, "prefilled_credential_field"))

    def assignment(target: ast.AST, value: ast.AST | None) -> None:
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for child, child_value in zip(target.elts, value.elts):
                assignment(child, child_value)
        else:
            check(_target_name(target), value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                assignment(target, node.value)
        elif isinstance(node, ast.AnnAssign):
            assignment(node.target, node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    check(key.value, value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            positional = [*node.args.posonlyargs, *node.args.args]
            for argument, value in zip(
                positional[-len(node.args.defaults) :] if node.args.defaults else [], node.args.defaults
            ):
                check(argument.arg, value)
            for argument, value in zip(node.args.kwonlyargs, node.args.kw_defaults):
                check(argument.arg, value)
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg:
                    check(keyword.arg, keyword.value)
            if _target_name(node.func) == "TwitchClient" and node.args:
                check("client_id", node.args[0])
            if _target_name(node.func) in {"get", "getenv"} and len(node.args) >= 2:
                name = _literal_string(node.args[0])
                if isinstance(name, str):
                    check(name, node.args[1])


@dataclass(frozen=True)
class _RootPath:
    relative: str = ""


class _NotDeclarative(ValueError):
    pass


def _spec_value(node: ast.AST, env: dict[str, Any]) -> Any:
    """Evaluate only literals and bounded root-relative path expressions, never code."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, bool, type(None))):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_spec_value(element, env) for element in node.elts]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        parent, child = _spec_value(node.left, env), _spec_value(node.right, env)
        if isinstance(parent, _RootPath) and isinstance(child, str):
            path = PurePosixPath(child)
            if path.is_absolute() or ".." in path.parts or "\\" in child or ":" in child:
                raise _NotDeclarative
            return _RootPath(str(PurePosixPath(parent.relative) / path))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "str"
        and len(node.args) == 1
        and not node.keywords
    ):
        return _spec_value(node.args[0], env)
    if isinstance(node, ast.ListComp) and len(node.generators) == 1:
        generator = node.generators[0]
        if isinstance(generator.target, ast.Name) and not generator.ifs and not generator.is_async:
            values = _spec_value(generator.iter, env)
            if isinstance(values, list) and len(values) <= 128:
                return [_spec_value(node.elt, {**env, generator.target.id: value}) for value in values]
    raise _NotDeclarative


def _check_spec(path: Path, root: Path, findings: list[Finding]) -> None:
    text = _read_text(path, root, findings)
    if text is None:
        return
    display = _display_path(path, root)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        findings.append(Finding(display, "packaging_spec_unparseable"))
        return
    _content_checks(text, display, findings, docs=False)
    _credential_ast_checks(tree, display, findings)
    env: dict[str, Any] = {}
    analyses: list[tuple[ast.Call, dict[str, Any]]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            # The repository's spec is in packaging/; SPECPATH's parent is root.
            if ast.dump(node.value) == ast.dump(ast.parse("Path(SPECPATH).parent", mode="eval").body):
                env[name] = _RootPath()
            else:
                try:
                    env[name] = _spec_value(node.value, env)
                except _NotDeclarative:
                    env.pop(name, None)
        for nested in ast.walk(node):
            if isinstance(nested, ast.Call) and _target_name(nested.func) == "Analysis":
                analyses.append((nested, dict(env)))
            if isinstance(nested, ast.Call) and _target_name(nested.func) in {
                "Tree",
                "collect_data_files",
                "collect_all",
                "glob",
                "rglob",
            }:
                findings.append(Finding(display, "packaging_dynamic_collection_not_allowed"))
            if isinstance(nested, ast.AugAssign):
                findings.append(Finding(display, "packaging_post_analysis_mutation_not_allowed"))
            if isinstance(nested, ast.Call) and _target_name(nested.func) in {
                "append",
                "extend",
                "insert",
                "update",
            }:
                findings.append(Finding(display, "packaging_post_analysis_mutation_not_allowed"))
    if len(analyses) != 1:
        findings.append(Finding(display, "exactly_one_packaging_analysis_required"))
        return
    analysis, env = analyses[0]
    keywords = {keyword.arg: keyword.value for keyword in analysis.keywords}
    try:
        if len(analysis.args) != 1 or _spec_value(analysis.args[0], env) != [_RootPath(ENTRYPOINT)]:
            raise _NotDeclarative
        if _spec_value(keywords["pathex"], env) != [_RootPath("src")]:
            raise _NotDeclarative
        for name in ("binaries", "hookspath", "runtime_hooks"):
            if _spec_value(keywords[name], env) != []:
                raise _NotDeclarative
    except (KeyError, _NotDeclarative):
        findings.append(Finding(display, "packaging_entrypoint_or_extra_code_outside_allowlist"))
    try:
        data = _spec_value(keywords["datas"], env)
        if not isinstance(data, list):
            raise _NotDeclarative
        for pair in data:
            if not isinstance(pair, list) or len(pair) != 2:
                raise _NotDeclarative
            source, destination = pair
            if (
                not isinstance(source, _RootPath)
                or source.relative not in PUBLIC_DOCS
                or destination != "docs"
            ):
                raise _NotDeclarative
            if not (root / source.relative).is_file():
                findings.append(Finding(source.relative, "allowlisted_document_missing"))
    except (KeyError, _NotDeclarative):
        findings.append(Finding(display, "packaging_data_must_be_explicit_public_documents"))


def _tracked_files(root: Path) -> Iterable[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode:
        return ()
    return tuple(name for name in result.stdout.decode("utf-8", errors="replace").split("\0") if name)


def scan_repository(root: Path, *, tracked_paths: Iterable[str] | None = None) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    paths = list((root / APP_SOURCE).rglob("*.py")) + [root / ENTRYPOINT]
    if not (root / APP_SOURCE).is_dir():
        findings.append(Finding(APP_SOURCE, "application_source_missing"))
    for path in (root / APP_SOURCE).rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            findings.append(Finding(_display_path(path, root), "build_input_escapes_root"))
    for path in paths:
        display = _display_path(path, root)
        if _forbidden_filename(display):
            findings.append(Finding(display, "forbidden_application_input_filename"))
        text = _read_text(path, root, findings)
        if text is None:
            continue
        _content_checks(text, display, findings, docs=False)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            findings.append(Finding(display, "application_source_unparseable"))
            continue
        _credential_ast_checks(tree, display, findings)
    for name in PUBLIC_DOCS:
        path = root / name
        text = _read_text(path, root, findings)
        if text is not None:
            _content_checks(text, name, findings, docs=True)
    for name in _tracked_files(root) if tracked_paths is None else tracked_paths:
        if _forbidden_filename(name):
            findings.append(Finding(name, "forbidden_tracked_credential_or_user_file"))
        path = root / name
        if path.suffix.lower() == ".pem" and path.is_file():
            text = _read_text(path, root, findings)
            if text is not None:
                _content_checks(text, name, findings, docs=True)
    _check_spec(root / SPEC_PATH, root, findings)
    return sorted(set(findings))


def scan_distribution(directory: Path) -> list[Finding]:
    directory = directory.resolve()
    if not directory.is_dir():
        return [Finding(directory.name, "distribution_directory_missing")]
    findings: list[Finding] = []
    for path in directory.rglob("*"):
        display = _display_path(path, directory)
        if path.is_symlink() and not path.resolve().is_relative_to(directory):
            findings.append(Finding(display, "distribution_symlink_escapes_root"))
            continue
        if not path.is_file():
            continue
        if _forbidden_filename(display, distribution=True):
            findings.append(Finding(display, "forbidden_distribution_credential_or_user_file"))
        if path.suffix.lower() in _TEXT_SUFFIXES:
            text = _read_text(path, directory, findings)
            if text is not None:
                _content_checks(text, display, findings, docs=True)
    return sorted(set(findings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dist", type=Path, help="Also scan an unpacked PyInstaller distribution directory.")
    args = parser.parse_args(argv)
    findings = scan_repository(args.root)
    if args.dist is not None:
        findings.extend(scan_distribution(args.dist))
    for finding in sorted(set(findings)):
        filename = finding.path.encode("unicode_escape").decode("ascii")
        print(f"{filename}: {finding.rule}")
    if findings:
        print("Distribution boundary check failed. Matched values have been withheld.")
        return 1
    print(
        "Distribution boundary checks passed (bounded source/packaging checks; not a complete secret audit)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
