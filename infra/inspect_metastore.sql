-- 直接看 MetaStore 里面到底是什么
--
-- 用法：
--   docker exec -i lab-postgres psql -U hive -d metastore < infra/inspect_metastore.sql
-- 或者进交互模式一条条敲：
--   docker exec -it lab-postgres psql -U hive -d metastore
--
-- 目的：证明「建一张 Hive 表 = 往这几张表里插几行」，
--       而不是「创建了一个数据库对象」。笔记 §8.3 / §8.6 讲的就在这里。
--
-- 注意：Hive 在 PostgreSQL 上把表名建成「带引号的大写」，所以必须写 "DBS"，
--       写 dbs 会报 relation does not exist。这是 MySQL 上不会遇到的坑。

\echo ''
\echo '==================== 1. 库（DBS）===================='
\echo '每个 CREATE DATABASE 就是插一行。注意 DB_LOCATION_URI 已经是 Windows 路径。'
SELECT "DB_ID", "NAME", "DB_LOCATION_URI" FROM "DBS" ORDER BY "DB_ID";


\echo ''
\echo '==================== 2. 表（TBLS）===================='
\echo '每张表一行。TBL_TYPE: MANAGED_TABLE（内部表）/ EXTERNAL_TABLE（外部表）。'
\echo '注意：整张表在 TBLS 里只是「一行」，字段和数据文件都不在这里。'
SELECT t."TBL_ID", d."NAME" AS db, t."TBL_NAME", t."TBL_TYPE", t."OWNER"
FROM "TBLS" t JOIN "DBS" d ON t."DB_ID" = d."DB_ID"
ORDER BY t."TBL_ID";


\echo ''
\echo '==================== 3. 表名 → 物理路径（核心那次 join）===================='
\echo 'SDS 是存储描述，LOCATION 就是那个目录。这条查询回答：这张表的数据在哪个文件夹？'
SELECT d."NAME" || '.' || t."TBL_NAME" AS full_table_name,
       s."LOCATION",
       s."INPUT_FORMAT"
FROM "TBLS" t
JOIN "DBS" d ON t."DB_ID" = d."DB_ID"
JOIN "SDS" s ON t."SD_ID" = s."SD_ID"
ORDER BY t."TBL_ID";


\echo ''
\echo '==================== 4. 字段（COLUMNS_V2）===================='
\echo '每个字段一行。INTEGER_IDX 是字段顺序，TYPE_NAME 是类型。'
SELECT t."TBL_NAME", c."INTEGER_IDX" AS pos, c."COLUMN_NAME", c."TYPE_NAME"
FROM "TBLS" t
JOIN "SDS" s ON t."SD_ID" = s."SD_ID"
JOIN "COLUMNS_V2" c ON s."CD_ID" = c."CD_ID"
ORDER BY t."TBL_NAME", c."INTEGER_IDX";


\echo ''
\echo '==================== 5. 分区（PARTITIONS）===================='
\echo '每个分区一行。写入 dt=2026-10-20 就是插一行 + 建一个目录。'
\echo '十万个分区 = 这张表十万行 —— 这就是「分区太多会拖垮 Metastore」的物理原因。'
SELECT d."NAME" || '.' || t."TBL_NAME" AS full_table_name,
       p."PART_ID",
       s."LOCATION" AS partition_path
FROM "PARTITIONS" p
JOIN "TBLS" t ON p."TBL_ID" = t."TBL_ID"
JOIN "DBS"  d ON t."DB_ID" = d."DB_ID"
JOIN "SDS"  s ON p."SD_ID" = s."SD_ID"
ORDER BY p."PART_ID";


\echo ''
\echo '==================== 6. 分区值（PARTITION_KEY_VALS）===================='
\echo '分区键与值的对应关系。PARTITION_KEY_VALS 存「dt=2026-10-20」里的 2026-10-20。'
SELECT p."PART_ID", k."PKEY_NAME", v."PART_KEY_VAL"
FROM "PARTITIONS" p
JOIN "PARTITION_KEY_VALS" v ON p."PART_ID" = v."PART_ID"
JOIN "PARTITION_KEYS" k     ON p."TBL_ID" = k."TBL_ID" AND v."INTEGER_IDX" = k."INTEGER_IDX"
ORDER BY p."PART_ID";


\echo ''
\echo '==================== 7. 规模统计 ===================='
\echo '想看「元数据有多少行」用这个。生产上一张百万分区的表会让这些数字很大。'
SELECT 'DBS' AS table_name, count(*) FROM "DBS"
UNION ALL SELECT 'TBLS',        count(*) FROM "TBLS"
UNION ALL SELECT 'COLUMNS_V2',  count(*) FROM "COLUMNS_V2"
UNION ALL SELECT 'PARTITIONS',  count(*) FROM "PARTITIONS"
UNION ALL SELECT 'SDS',         count(*) FROM "SDS"
ORDER BY table_name;
