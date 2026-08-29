import os
import pathlib
import sys

os.environ.pop("AWS_PROFILE", None)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bot"))
