"""
chat.py — Interactive Bio-Sentry prompt loop
Run: .venv/bin/python chat.py
"""
import asyncio
import logging
from agent import run


class PolicyLogger(logging.Handler):
    """Intercepts sondera log messages and prints them in a readable format."""

    PREFIXES = {
        logging.DEBUG:   "  [cedar] ",
        logging.INFO:    "  [cedar] ",
        logging.WARNING: "  [POLICY VIOLATION] ",
    }

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        # Filter out noisy trajectory ID bookkeeping lines
        if any(skip in msg for skip in ("Initialized trajectory", "finalized", "session")):
            return
        prefix = self.PREFIXES.get(record.levelno, "  [cedar] ")
        print(f"{prefix}{msg}")


def _setup_policy_logging() -> None:
    handler = PolicyLogger()
    for name in ("sondera.langgraph.middleware", "sondera.harness.cedar.harness"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.propagate = False


async def main() -> None:
    _setup_policy_logging()
    print("\n Bio-Sentry Agent — Interactive Mode (verbose policy logging ON)")
    print("Type your prompt and press Enter. Type 'exit' to quit.\n")
    while True:
        try:
            prompt = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if not prompt:
            continue
        if prompt.lower() in ("exit", "quit"):
            print("Bye.")
            break
        print()
        response = await run(prompt)
        print(f"\nAgent: {response}\n")


if __name__ == "__main__":
    asyncio.run(main())
