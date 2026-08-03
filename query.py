"""
query.py - turns a raw user query into normalized tokens.

Deliverately thin: it reuses the SAME tokenize() the parser used on documents,
so queries and documents are normalized identically. That symmetry is what makes
matching possible.
"""

from parser import tokenize

def process_query(raw_query: str) -> list[str]:
    """Normalize a user's query into a list of tokens."""
    return tokenize(raw_query)

if __name__ == "__main__":
    print(process_query("Best PYTHON web-development!"))
    # -> ['best', 'python', 'web', 'development']