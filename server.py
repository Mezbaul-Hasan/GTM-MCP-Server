"""Runner for `python server.py` and `mcp dev server.py` (PROJECT.md §3)."""

from gtm_mcp.server import create_server

mcp = create_server()

if __name__ == "__main__":
    mcp.run(transport="stdio")
