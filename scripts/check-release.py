#!/usr/bin/env python3
from release_support import check_record
record = check_record()
print(f'Compatibility record consistent: {record["package_version"]}, runtime {record["runtime"]["commit"]}')
