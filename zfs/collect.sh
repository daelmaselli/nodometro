#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
case "${1:-}" in
  pools)
    /usr/sbin/zpool list -Hp -o name,health,size,alloc,free,capacity,fragmentation |
      awk -F '\t' '
        function escape(s) { gsub(/\\/, "\\\\", s); gsub(/,/, "\\,", s); gsub(/ /, "\\ ", s); gsub(/=/, "\\=", s); return s }
        NF == 7 {
          health = ($2 == "ONLINE" ? 0 : ($2 == "DEGRADED" ? 1 : 2))
          printf "zfs_pool_status,pool=%s,health=%s health_code=%di,size_bytes=%si,allocated_bytes=%si,free_bytes=%si,capacity_percent=%s", escape($1), escape($2), health, $3, $4, $5, $6
          if ($7 != "-") printf ",fragmentation_percent=%s", $7
          printf "\n"
          next
        }
        NF { print "Invalid zpool list response" > "/dev/stderr"; exit 1 }
      '
    /usr/sbin/zpool status -Pp |
      awk '
        function escape(s) { gsub(/\\/, "\\\\", s); gsub(/,/, "\\,", s); gsub(/ /, "\\ ", s); gsub(/=/, "\\=", s); return s }
        $1 == "pool:" { pool=$2; config=0 }
        $1 == "config:" { config=1 }
        $1 == "errors:" { config=0 }
        config && NF == 5 && $3 ~ /^[0-9]+$/ && $4 ~ /^[0-9]+$/ && $5 ~ /^[0-9]+$/ {
          printf "zfs_vdev,pool=%s,vdev=%s,state=%s read_errors=%si,write_errors=%si,checksum_errors=%si\n", escape(pool), escape($1), escape($2), $3, $4, $5
          if ($1 == pool) printf "zfs_pool_errors,pool=%s read_errors=%si,write_errors=%si,checksum_errors=%si\n", escape(pool), $3, $4, $5
        }
      '
    ;;
  datasets)
    /usr/sbin/zfs list -Hp -t filesystem,volume -o name,used,available,usedbysnapshots,usedbydataset,usedbychildren,logicalused,compressratio |
      awk -F '\t' '
        function escape(s) { gsub(/\\/, "\\\\", s); gsub(/,/, "\\,", s); gsub(/ /, "\\ ", s); gsub(/=/, "\\=", s); return s }
        NF == 8 {
          pool=$1; sub(/\/.*/, "", pool)
          ratio=$8; sub(/x$/, "", ratio)
          printf "zfs_dataset_space,pool=%s,dataset=%s used_bytes=%si,available_bytes=%si,snapshot_used_bytes=%si,dataset_used_bytes=%si,children_used_bytes=%si,logical_used_bytes=%si,compression_ratio=%s\n", escape(pool), escape($1), $2, $3, $4, $5, $6, $7, ratio
          next
        }
        NF { print "Invalid zfs list response" > "/dev/stderr"; exit 1 }
      '
    ;;
  *) echo 'Usage: collect.sh pools|datasets' >&2; exit 64 ;;
esac
