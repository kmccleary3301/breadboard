from importlib import import_module
import sys


sys.modules[__name__] = import_module("agentic_coder_prototype.conductor.prompt_planner")
