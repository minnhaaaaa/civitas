import os

from hypothesis import settings

settings.register_profile("dev", max_examples=100, print_blob=True)
settings.register_profile("ci", derandomize=True, max_examples=200, print_blob=True)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))
