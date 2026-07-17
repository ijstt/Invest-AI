import sys
import os

# Add src to python path
sys.path.insert(0, '/home/ijstt/News/src')

from geoanalytics.processing.common import make_full_text

test_cases = [
    # (title, body, expected)
    (None, None, ""),
    ("", "", ""),
    ("Title", None, "Title."),
    ("Title.", None, "Title."),
    ("Title...", None, "Title..."),
    (None, "Body", "Body"),
    ("", "Body", "Body"),
    ("Title", "Body", "Title. Body"),
    ("Title.", "Body", "Title. Body"),
    ("Title...", "Body", "Title. Body"), # rstrip('.') removes all trailing periods
    ("Title", " Body", "Title. Body"),
    ("Title.", " Body", "Title. Body"),
    ("Title", "   Body", "Title.   Body"),
    ("   Title   ", "   Body   ", "Title.   Body"),
    ("\nTitle\n", "\nBody\n", "Title. \nBody"),
    ("Title", "  ", "Title."), # body_clean gets stripped to "", so return title_clean + "."
    ("Title", " \n ", "Title."), # body_clean gets stripped to "", so return title_clean + "."
]

print("Starting make_full_text boundary checks:")
failed = 0
for i, (title, body, expected) in enumerate(test_cases):
    res = make_full_text(title, body)
    if res != expected:
        print(f"FAIL: Case {i} - make_full_text({repr(title)}, {repr(body)})")
        print(f"  Expected: {repr(expected)}")
        print(f"  Got:      {repr(res)}")
        failed += 1
    else:
        print(f"PASS: Case {i} - make_full_text({repr(title)}, {repr(body)}) -> {repr(res)}")

if failed:
    print(f"\nResult: {failed} checks failed!")
    sys.exit(1)
else:
    print("\nResult: All checks passed successfully!")
    sys.exit(0)
