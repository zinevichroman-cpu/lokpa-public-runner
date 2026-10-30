# Security
- Workflow запускается только вручную (`workflow_dispatch`), без `pull_request` и автоматического триггера от публичных issues.
- `LOKPA_PRIVATE_REPO_TOKEN` должен быть fine-grained и ограничен приватным репозиторием; разрешения: Contents read-only, Issues read/write.
- Не помещать дату рождения, события, токены или результаты в публичные issues/коммиты/входные параметры.
- Не публиковать artifacts с приватным JSON. Текущий workflow их не создаёт.
