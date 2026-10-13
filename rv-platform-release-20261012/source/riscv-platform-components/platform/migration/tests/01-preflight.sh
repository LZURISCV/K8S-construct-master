#!/usr/bin/env bash
set -euo pipefail
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
task_config=${1:?控制器配置 JSON 路径}
python3 - "$task_dir" "$task_config" <<'PY'
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location('migration', pathlib.Path(sys.argv[1]) / 'controller.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
config = json.load(open(sys.argv[2])); transport = m.SSH(config)
for node in config['nodes']:
    print(node, json.dumps(transport.run(node, 'preflight'), ensure_ascii=False))
PY
