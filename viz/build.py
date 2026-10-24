"""Inline viz/trace.json into viz/template.html -> viz/animation.html (single self-contained file)."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
t = open(os.path.join(HERE, "template.html")).read()
d = json.dumps(json.load(open(os.path.join(HERE, "trace.json"))), separators=(",", ":"))
open(os.path.join(HERE, "animation.html"), "w").write(t.replace("/*TRACE*/null", d))
print("viz/animation.html", len(t) + len(d))
