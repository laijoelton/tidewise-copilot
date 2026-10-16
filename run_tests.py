"""
run_tests.py — Test runner for Tidewise.
Discovers and executes all tests in the tests/ directory.
Usage:
    python run_tests.py
"""

import sys
import unittest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main():
    print("=" * 70)
    print("🧪 Tidewise | Test Suite")
    print("=" * 70)
    
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_*.py")
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print("=" * 70)
    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    passed = total - failures - errors
    
    print(f"📊 Test Results Summary:")
    print(f"  Total Tests:  {total}")
    print(f"  Passed:       {passed} ({(passed/total*100):.1f}%)")
    print(f"  Failures:     {failures}")
    print(f"  Errors:       {errors}")
    print("=" * 70)
    
    if not result.wasSuccessful():
        sys.exit(1)
    else:
        print("✨ All regression tests passed successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()
