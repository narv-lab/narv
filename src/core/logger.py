import logging
import os
import sys

def setup_logger(module_name: str) -> logging.Logger:
    """Sets up a structured logger for a given module."""
    logger = logging.getLogger(module_name)
    
    # Avoid duplicate handlers if the logger is requested multiple times
    if not logger.handlers:
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        logger.setLevel(getattr(logging, log_level, logging.INFO))
        
        handler = logging.StreamHandler(sys.stdout)
        
        # Format includes caller_id / module contextual info conceptually
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - [%(levelname)s] - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger
