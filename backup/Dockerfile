# syntax=docker/dockerfile:1
# The official mongo image already ships mongodump and mongosh.
FROM mongo:7.0

ENV TZ=Asia/Jerusalem

COPY backup/backup.sh /usr/local/bin/backup.sh
RUN chmod +x /usr/local/bin/backup.sh

ENTRYPOINT ["/usr/local/bin/backup.sh"]
