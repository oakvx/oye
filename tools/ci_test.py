"""Run pytest and surface failed cases as GitHub check annotations."""
from __future__ import annotations
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        report = Path(temp) / 'pytest.xml'
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                                 '--junitxml', str(report)], check=False)
        if result.returncode and report.exists():
            root = ElementTree.parse(report).getroot()
            for case in root.iter('testcase'):
                for problem in case:
                    if problem.tag not in ('failure', 'error'):
                        continue
                    text = f"{case.get('classname')}.{case.get('name')}: {problem.get('message', '')}"
                    # GitHub workflow commands require percent/newline escaping.
                    text = text.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
                    print('::error::' + text[:3000], flush=True)
        return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
