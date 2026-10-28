"""Print the Python and numpy header/library paths as cmd SET lines, for build_npcache.bat."""
import os
import sys
import sysconfig

import numpy

print(f'set "PYINC={sysconfig.get_paths()["include"]}"')
print(f'set "PYLIB={os.path.join(sys.base_prefix, "libs")}"')
print(f'set "NPINC={numpy.get_include()}"')
