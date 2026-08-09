#!/usr/bin/env python3
"""
Script to generate openapi.json for Hermes tooling configuration.
Saves output to .agents/swarm/openapi.json
"""
import json
import sys
from pathlib import Path

# Add src/ to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
sys.path.insert(0, str(src_dir))

from mms4tk.api import app


def generate_openapi():
    output_dir = root_dir / ".agents" / "swarm"
    output_dir.mkdir(parents=True, exist_ok=True)

    openapi_schema = app.openapi()
    output_file = output_dir / "openapi.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(openapi_schema, f, indent=2)

    print(f"Successfully generated openapi.json at: {output_file}")


if __name__ == "__main__":
    generate_openapi()
