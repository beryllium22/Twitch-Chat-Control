"""Desktop entry point; importing the package never connects or sends input."""


def main() -> None:
    from .app import main as run_app

    run_app()


if __name__ == "__main__":
    main()
