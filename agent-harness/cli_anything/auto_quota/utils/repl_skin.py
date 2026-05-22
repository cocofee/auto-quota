from __future__ import annotations


class ReplSkin:
    """Small local fallback compatible with the CLI-Anything RePL contract."""

    def __init__(self, name: str, version: str = "0.1.0") -> None:
        self.name = name
        self.version = version

    def print_banner(self) -> None:
        print(f"{self.name} harness v{self.version}")
        print("Type 'help' for commands, 'exit' to quit.")

    def success(self, message: str) -> None:
        print(f"OK: {message}")

    def error(self, message: str) -> None:
        print(f"ERROR: {message}")

    def info(self, message: str) -> None:
        print(message)

    def help(self, commands: dict[str, str]) -> None:
        for name, desc in commands.items():
            print(f"  {name:<18} {desc}")
