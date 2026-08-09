"""Test package marker.

Present so `python -m unittest discover -s tests -t .` can import the start
directory. Without it discovery aborts with "Start directory is not importable"
and CI silently runs whatever handful of modules are named by hand -- which is
how a failing test sat on main while every workflow stayed green.
"""
