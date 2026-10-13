#!/usr/bin/env bash
# Delegate only the root-owned migration helper to the selected SSH account.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo '请使用 sudo 执行。' >&2; exit 1; }
task_user=${1:?用法: enable-sudo-helper.sh lzu}
[[ "$task_user" =~ ^[a-z_][a-z0-9_-]*$ ]] && id "$task_user" >/dev/null
command -v visudo >/dev/null
task_helper=/usr/local/libexec/rv-migrate-runtime
[[ -x "$task_helper" && ! -L "$task_helper" && $(stat -c '%u' "$task_helper") == 0 ]]
python3 - "$task_helper" <<'PY'
import os, sys
if os.stat(sys.argv[1]).st_mode & 0o022:
    raise SystemExit('迁移程序不能由普通用户或组写入。')
PY
task_temp=$(mktemp /etc/sudoers.d/.rv-migration.XXXXXXXX)
trap 'rm -f -- "$task_temp"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/local/libexec/rv-migrate-runtime *\n' "$task_user" > "$task_temp"
chmod 0440 "$task_temp"
visudo -cf "$task_temp"
mv -f -- "$task_temp" /etc/sudoers.d/rv-migration
echo "已允许 $task_user 通过 sudo -n 执行迁移程序；其他命令的 sudo 设置未修改。"
