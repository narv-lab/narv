import os
import sys
from src.core.logger import setup_logger
from src.core.di_container import container

logger = setup_logger("main")


def main():
    import signal
    logger.info("Starting Narv System Initialization...")

    def handle_sigterm(signum, frame):
        logger.info("Received signal %s. Initiating graceful shutdown...", signum)
        import sys
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    try:
        # Wire all modules
        container.wire()
        logger.info("System Initialized Successfully.")

        # Start Kernel main loop
        if container.kernel:
            max_cycles_env = os.getenv("MAX_CYCLES")
            max_cycles = int(max_cycles_env) if max_cycles_env else None
            logger.info("Starting kernel AF_loop_selfdriven... max_cycles=%s", max_cycles)
            container.kernel.start(max_cycles=max_cycles)
        else:
            logger.warning("Kernel not initialized. Exiting clean.")

    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down gracefully.")
    except Exception as e:
        logger.critical("Failed to start system: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        # Close memory connections
        if container.memory:
            try:
                container.memory.close()
            except Exception:
                pass
        logger.info("Narv system shutdown complete.")


if __name__ == "__main__":
    main()

