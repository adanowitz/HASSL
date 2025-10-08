# hassl/parser/loader.py
from importlib.resources import files

def load_grammar_text():
    """Read the embedded hassl.lark grammar safely from the installed package."""
    return (files("hassl.parser") / "hassl.lark").read_text(encoding="utf-8")
