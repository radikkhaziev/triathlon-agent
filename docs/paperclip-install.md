# Установка Paperclip на собственный сервер

Полный мануал деплоя Paperclip на Ubuntu-сервер (DigitalOcean droplet) за Caddy с автоматическим HTTPS, PostgreSQL и systemd.

---

## 0. Что должно быть готово до старта

- Дроплет Ubuntu 22.04+ (минимум 2 vCPU / 4GB RAM)
- Домен с A-записью на IP сервера (например `paperclip.endurai.me`)
- SSH-ключ на локальной машине

---

## 1. Базовая настройка сервера

Заходишь как root:

```bash
ssh root@<IP>
```

Создаёшь пользователя `paperclip`, добавляешь в sudo, настраиваешь firewall:

```bash
adduser paperclip
usermod -aG sudo paperclip

# скопировать SSH-ключ root → paperclip
rsync --archive --chown=paperclip:paperclip ~/.ssh /home/paperclip

# firewall
ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw enable
```

## 2. Безопасность SSH

**Открой второй SSH-коннект как страховку — не закрывай его, пока не убедишься что всё работает.**

Бэкап и редактирование `sshd_config`:

```bash
sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

Проверь из нового терминала:

```bash
ssh paperclip@<IP> 'echo "key auth works"'
ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password paperclip@<IP>
# второй должен дать Permission denied (publickey)
```

Если что-то сломалось — через резервный коннект откатываешь:

```bash
sudo cp /etc/ssh/sshd_config.backup /etc/ssh/sshd_config
sudo systemctl restart ssh
```

---

## 3. Node.js 20 + pnpm

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

node -v   # v20.x
npm -v

sudo corepack enable
corepack prepare pnpm@9 --activate
pnpm -v
```

---

## 4. PostgreSQL 17

```bash
sudo apt install -y postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt install -y postgresql-17
```

Сгенерируй пароль и **сохрани в парольник**:

```bash
openssl rand -base64 32
```

Создай юзера и БД:

```bash
sudo -u postgres psql <<EOF
CREATE USER paperclip WITH PASSWORD 'СГЕНЕРИРОВАННЫЙ_ПАРОЛЬ';
CREATE DATABASE paperclip OWNER paperclip;
GRANT ALL PRIVILEGES ON DATABASE paperclip TO paperclip;
EOF
```

Проверь:

```bash
psql "postgresql://paperclip:ПАРОЛЬ@localhost:5432/paperclip" -c "SELECT version();"
```

---

## 5. Claude Code CLI (опционально, если нужен на сервере)

```bash
sudo npm install -g @anthropic-ai/claude-code
claude --version
claude login
```

Скопируй URL из `claude login`, открой на локальной машине, авторизуйся под Max-аккаунтом, вставь код в терминал сервера.

Тест:

```bash
echo "say hello" | claude --print -
```

---

## 6. Установка Paperclip

```bash
cd ~
git clone https://github.com/paperclipai/paperclip.git
cd paperclip
pnpm install
```

### 6.1 Сборка фронта (КРИТИЧНО для production)

`pnpm paperclipai run` стартует Vite dev-сервер, что не подходит для прода. Нужно собрать UI:

```bash
pnpm build
```

Проверь, что бандл создан:

```bash
ls -la ui/dist/
ls -la server/dist/
```

Должны быть `index.html` и папка `assets/`. Если в `package.json` есть скрипт `prepare:ui-dist` — выполни его, он копирует UI в `server/ui-dist/`, откуда server раздаёт статику.

### 6.2 Onboarding

```bash
pnpm paperclipai onboard
```

Отвечай мастеру:

| Вопрос | Ответ |
|--------|-------|
| Reachability template | **Custom** |
| Database | External Postgres → `postgresql://paperclip:ПАРОЛЬ@localhost:5432/paperclip` |
| Auth mode | `authenticated` |
| Exposure | `public` |
| Public URL | `https://paperclip.endurai.me` |
| Host bind | `127.0.0.1` |
| Port | `3100` |

Прогон доктора:

```bash
pnpm paperclipai doctor
```

Должен сказать "ready".

### 6.3 Allowed hostname

```bash
pnpm paperclipai allowed-hostname paperclip.endurai.me
```

---

## 7. systemd-юнит

```bash
sudo tee /etc/systemd/system/paperclip.service > /dev/null <<'EOF'
[Unit]
Description=Paperclip control plane
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=paperclip
WorkingDirectory=/home/paperclip/paperclip
Environment="NODE_ENV=production"
Environment="HOST=127.0.0.1"
Environment="PORT=3100"
Environment="PAPERCLIP_DEPLOYMENT_MODE=authenticated"
Environment="PAPERCLIP_SECRETS_STRICT_MODE=true"
Environment="TRUST_PROXY=true"
ExecStart=/usr/bin/pnpm paperclipai run
Restart=on-failure
RestartSec=10
StandardOutput=append:/var/log/paperclip/out.log
StandardError=append:/var/log/paperclip/err.log

[Install]
WantedBy=multi-user.target
EOF

sudo mkdir -p /var/log/paperclip
sudo chown paperclip:paperclip /var/log/paperclip

sudo systemctl daemon-reload
sudo systemctl enable paperclip
sudo systemctl start paperclip
sudo systemctl status paperclip
```

Логи:

```bash
journalctl -u paperclip -f
```

Если своему юзеру не видны логи systemd:

```bash
sudo usermod -aG adm,systemd-journal paperclip
# выйти и зайти заново
```

---

## 8. Caddy (reverse proxy + Let's Encrypt)

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

Конфиг:

```bash
sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
paperclip.endurai.me {
    reverse_proxy 127.0.0.1:3100
    encode gzip

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
    }
}
EOF

sudo systemctl reload caddy
journalctl -u caddy -f
```

Caddy сам получит сертификат Let's Encrypt при первом запросе.

---

## 9. Регистрация CEO

Сгенерируй invite для bootstrap-аккаунта:

```bash
cd ~/paperclip
pnpm paperclipai auth bootstrap-ceo --base-url https://paperclip.endurai.me --force --expires-hours 24
```

Открой полученный URL в браузере, зарегистрируйся через Better Auth, попадаешь в дашборд.

Альтернативно — board-claim из логов:

```bash
journalctl -u paperclip -n 500 | grep -i "board-claim"
```

---

## 10. Финальные хвосты

### 10.1 Бэкап master key (КРИТИЧНО)

Этот файл шифрует все секреты в инстансе. Потеряешь — потеряешь все API-ключи и токены агентов:

```bash
cat ~/.paperclip/instances/default/secrets/master.key
chmod 600 ~/.paperclip/instances/default/secrets/master.key
```

Скопируй содержимое в **1Password / Bitwarden** под именем `Paperclip master key — paperclip.endurai.me`. Не в заметки, не в чат.

### 10.2 Автобэкап PostgreSQL

Paperclip делает свои бэкапы каждые ~170 минут в `~/.paperclip/.../data/backups`, но дублирующий cron вне инстанса полезен:

```bash
mkdir -p ~/backups
crontab -e
```

Добавь:

```
0 3 * * * pg_dump "postgresql://paperclip:ПАРОЛЬ@localhost:5432/paperclip" | gzip > ~/backups/paperclip_$(date +\%Y\%m\%d).sql.gz
0 4 * * * find ~/backups -name "paperclip_*.sql.gz" -mtime +14 -delete
```

### 10.3 Smoke-тест

- Открой `https://paperclip.endurai.me` → должен загрузиться UI
- Залогинься под CEO → попасть в дашборд
- Создай первую задачу → проверь что pipeline (heartbeat → checkout → execute → результат) работает

---

## Troubleshooting

### Браузер ругается на Vite-файлы (`/@vite/client`, MIME `text/plain`)

Это значит сервер запущен в dev-режиме. Решение — пересобрать UI:

```bash
cd ~/paperclip
pnpm build
ls -la server/ui-dist/   # должно быть не пусто
sudo systemctl restart paperclip
journalctl -u paperclip -n 80 --no-pager | grep -iE "uimode|vite|static"
```

В логах ищи `uiMode=static`. Если всё ещё `vite-dev` — проверь, что `NODE_ENV=production` в systemd-юните.

### Mixed content / HTTPS-ссылки превращаются в HTTP

Caddy форвардит, но Express не доверяет proxy. В systemd-юнит уже добавлен `TRUST_PROXY=true`. Если проблема осталась — проверь Caddyfile (`reverse_proxy 127.0.0.1:3100` сам ставит `X-Forwarded-Proto: https`).

### `node server/dist/index.js` падает с ошибками импорта

Workspace-пакеты (`@paperclipai/db` и т.д.) линкуются как симлинки на `src/index.ts`, Node не понимает TS. Запускай через `pnpm paperclipai run` или `pnpm --filter server start` после полного `pnpm build`.

### journalctl не показывает логи paperclip

```bash
sudo usermod -aG adm,systemd-journal paperclip
exit
ssh paperclip@<IP>
groups   # должны быть adm и systemd-journal
```
