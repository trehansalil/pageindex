"""Backward-compatible entry point. Delegates to the pageindex_mcp package."""
from pageindex_mcp.server import main

if __name__ == "__main__":
    main()
