-- ============================================================
-- 农业物联网看板 - PostgreSQL 数据库初始化脚本
-- ============================================================
-- 说明：
--   1. 本脚本支持幂等执行，所有表均使用 DROP ... IF EXISTS 先删除再创建
--   2. 核心表 sensor_data 采用按月 RANGE 分区策略
--   3. 所有注释均使用中文
-- ============================================================


-- -----------------------------------------------------------
-- 1. 设备表（devices）
-- 存储所有物联网设备的基本信息，包括设备编码、所属温室、通信协议和在线状态
-- -----------------------------------------------------------
DROP TABLE IF EXISTS alert_logs CASCADE;
DROP TABLE IF EXISTS alert_rules CASCADE;
DROP TABLE IF EXISTS sensor_data CASCADE;
DROP TABLE IF EXISTS devices CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE devices (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    device_code     VARCHAR(64)     NOT NULL UNIQUE,
    device_name     VARCHAR(128)    NOT NULL,
    greenhouse_id   VARCHAR(64)     NOT NULL,
    protocol        VARCHAR(32)     NOT NULL DEFAULT 'mqtt',
    status          VARCHAR(32)     NOT NULL DEFAULT 'online',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE  devices IS '设备表 - 存储物联网采集设备的基本信息';
COMMENT ON COLUMN devices.id            IS '设备唯一标识，使用 UUID 自动生成';
COMMENT ON COLUMN devices.device_code   IS '设备编码，业务唯一键，用于与硬件设备对应';
COMMENT ON COLUMN devices.device_name   IS '设备名称，便于展示和识别';
COMMENT ON COLUMN devices.greenhouse_id IS '所属温室编号，用于按温室筛选设备';
COMMENT ON COLUMN devices.protocol      IS '通信协议，默认 mqtt，可扩展为 coap / http 等';
COMMENT ON COLUMN devices.status        IS '设备状态，如 online / offline / fault';
COMMENT ON COLUMN devices.created_at    IS '记录创建时间';
COMMENT ON COLUMN devices.updated_at    IS '记录最后更新时间';


-- -----------------------------------------------------------
-- 2. 传感器数据表（sensor_data）—— 核心分区表
-- -----------------------------------------------------------
-- 【分区策略详解】
--   本表是整个系统最核心的表，数据量增长最快。每个设备每隔数秒就会上报一条采集记录，
--   随着设备和时间的增加，单表数据量极易达到数亿甚至数十亿行，严重影响查询性能。
--
--   采用 PostgreSQL 原生 RANGE 分区，按 collected_at（采集时间）以月为粒度划分：
--     - 每个月的数据存储在独立的分区子表中
--     - 查询带时间范围条件时，PostgreSQL 可通过分区裁剪（Partition Pruning）
--       只扫描相关分区，避免全表扫描，大幅提升查询速度
--     - 历史数据归档/删除时可直接 DROP 整个分区，效率远高于 DELETE
--     - 每个分区可独立进行 VACUUM、ANALYZE，减少维护开销
--
--   【为何选择按月分区？】
--     - 按日分区：分区数量过多，管理复杂，且单分区数据量偏小
--     - 按年分区：单分区数据量过大，查询裁剪效果不明显
--     - 按月分区：在管理复杂度和查询性能之间取得最佳平衡，
--       单月数据量适中，足以支撑常见的时间范围查询场景
--
--   【如何新增分区？】
--     每月需提前创建下个月的分区，执行如下 SQL 即可：
--       CREATE TABLE sensor_data_y2025m04 PARTITION OF sensor_data
--         FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
--     建议通过定时任务（cron / pg_cron）在每月最后一天自动创建下月分区。
--     也可使用 pg_partman 扩展实现自动分区管理。
--
--   【分区键选择】
--     collected_at 是传感器数据最天然的查询维度，几乎所有业务查询都会携带时间范围条件，
--     因此选其作为分区键可最大化分区裁剪收益。
-- -----------------------------------------------------------
CREATE TABLE sensor_data (
    id              BIGSERIAL,
    device_id       UUID            NOT NULL,
    metric_type     VARCHAR(32)     NOT NULL,
    metric_value    DOUBLE PRECISION NOT NULL,
    collected_at    TIMESTAMPTZ     NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    PRIMARY KEY (id, collected_at)
) PARTITION BY RANGE (collected_at);

COMMENT ON TABLE  sensor_data IS '传感器数据表（按月 RANGE 分区）- 存储设备采集的温度、湿度、光照、pH 等指标时序数据';
COMMENT ON COLUMN sensor_data.id           IS '数据行 ID，使用 BIGSERIAL 自增，与 collected_at 组成复合主键以满足分区表要求';
COMMENT ON COLUMN sensor_data.device_id    IS '采集设备 ID，关联 devices.id';
COMMENT ON COLUMN sensor_data.metric_type  IS '指标类型，如 temperature / humidity / light / ph';
COMMENT ON COLUMN sensor_data.metric_value IS '采集值，浮点数';
COMMENT ON COLUMN sensor_data.collected_at IS '数据采集时间，同时作为分区键，查询时务必携带时间范围以触发分区裁剪';
COMMENT ON COLUMN sensor_data.created_at   IS '记录入库时间，默认当前时间';

-- ---- 分区：2025 年 1 月 ----
CREATE TABLE sensor_data_y2025m01 PARTITION OF sensor_data
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');

-- ---- 分区：2025 年 2 月 ----
CREATE TABLE sensor_data_y2025m02 PARTITION OF sensor_data
    FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');

-- ---- 分区：2025 年 3 月 ----
CREATE TABLE sensor_data_y2025m03 PARTITION OF sensor_data
    FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');

-- ---- sensor_data 索引 ----
-- 分区表上的索引会自动应用于所有现有及未来的分区子表

-- 按设备+时间范围查询：查某设备在指定时间段内的采集数据（最常用查询模式）
CREATE INDEX idx_sensor_data_device_time ON sensor_data (device_id, collected_at);

-- 按指标类型+时间范围查询：查某类指标在指定时间段内的数据（如查看所有温度数据趋势）
CREATE INDEX idx_sensor_data_metric_time ON sensor_data (metric_type, collected_at);

-- 按时间范围查询：支持无设备/指标筛选的纯时间范围查询
CREATE INDEX idx_sensor_data_collected_at ON sensor_data (collected_at);

-- 按设备+指标类型+时间范围：组合筛选场景（如查某设备的温度数据）
CREATE INDEX idx_sensor_data_device_metric_time ON sensor_data (device_id, metric_type, collected_at);

-- 外键约束：sensor_data.device_id -> devices.id
-- 注意：分区表上的外键需在每个分区子表上单独创建，此处通过触发器或应用层保证引用完整性
-- 若不需要数据库级外键强制，可省略以提升写入性能

ALTER TABLE sensor_data
    ADD CONSTRAINT fk_sensor_data_device
    FOREIGN KEY (device_id) REFERENCES devices(id);


-- -----------------------------------------------------------
-- 3. 告警规则表（alert_rules）
-- 定义传感器数据告警触发条件，支持按温室范围或全局生效
-- -----------------------------------------------------------
CREATE TABLE alert_rules (
    id              SERIAL          PRIMARY KEY,
    rule_name       VARCHAR(128)    NOT NULL,
    metric_type     VARCHAR(32)     NOT NULL,
    operator        VARCHAR(8)      NOT NULL,
    threshold_value DOUBLE PRECISION NOT NULL,
    greenhouse_id   VARCHAR(64),
    enabled         BOOLEAN         NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE  alert_rules IS '告警规则表 - 定义传感器数据超限告警的触发条件';
COMMENT ON COLUMN alert_rules.id             IS '规则 ID，自增主键';
COMMENT ON COLUMN alert_rules.rule_name      IS '规则名称，便于识别和管理';
COMMENT ON COLUMN alert_rules.metric_type    IS '监控指标类型，如 temperature / humidity / light / ph';
COMMENT ON COLUMN alert_rules.operator       IS '比较运算符：gt(大于) / lt(小于) / gte(大于等于) / lte(小于等于) / eq(等于)';
COMMENT ON COLUMN alert_rules.threshold_value IS '告警阈值，与 operator 配合判断是否触发告警';
COMMENT ON COLUMN alert_rules.greenhouse_id  IS '生效温室编号，为 NULL 时表示该规则对所有温室生效';
COMMENT ON COLUMN alert_rules.enabled        IS '是否启用，默认 true';
COMMENT ON COLUMN alert_rules.created_at     IS '规则创建时间';
COMMENT ON COLUMN alert_rules.updated_at     IS '规则最后更新时间';


-- -----------------------------------------------------------
-- 4. 告警日志表（alert_logs）
-- 记录每次告警触发的详细信息，用于历史回溯和分析
-- -----------------------------------------------------------
CREATE TABLE alert_logs (
    id              BIGSERIAL        PRIMARY KEY,
    device_id       UUID             NOT NULL,
    rule_id         INTEGER          NOT NULL,
    alert_message   TEXT             NOT NULL,
    metric_value    DOUBLE PRECISION NOT NULL,
    threshold_value DOUBLE PRECISION NOT NULL,
    alert_time      TIMESTAMPTZ      NOT NULL,
    acknowledged    BOOLEAN          NOT NULL DEFAULT false
);

COMMENT ON TABLE  alert_logs IS '告警日志表 - 记录每次告警触发的详细信息';
COMMENT ON COLUMN alert_logs.id              IS '日志 ID，BIGSERIAL 自增主键，支撑大量告警记录';
COMMENT ON COLUMN alert_logs.device_id        IS '触发告警的设备 ID，关联 devices.id';
COMMENT ON COLUMN alert_logs.rule_id          IS '触发的告警规则 ID，关联 alert_rules.id';
COMMENT ON COLUMN alert_logs.alert_message    IS '告警消息内容，包含具体的超限描述信息';
COMMENT ON COLUMN alert_logs.metric_value     IS '告警时的实际采集值';
COMMENT ON COLUMN alert_logs.threshold_value  IS '告警规则的阈值';
COMMENT ON COLUMN alert_logs.alert_time       IS '告警触发时间';
COMMENT ON COLUMN alert_logs.acknowledged     IS '是否已确认/处理，默认 false';

ALTER TABLE alert_logs
    ADD CONSTRAINT fk_alert_logs_device
    FOREIGN KEY (device_id) REFERENCES devices(id);

ALTER TABLE alert_logs
    ADD CONSTRAINT fk_alert_logs_rule
    FOREIGN KEY (rule_id) REFERENCES alert_rules(id);

CREATE INDEX idx_alert_logs_device ON alert_logs (device_id);
CREATE INDEX idx_alert_logs_rule   ON alert_logs (rule_id);
CREATE INDEX idx_alert_logs_time   ON alert_logs (alert_time);


-- -----------------------------------------------------------
-- 5. 用户表（users）
-- 存储系统登录用户的账户信息
-- -----------------------------------------------------------
CREATE TABLE users (
    id              SERIAL          PRIMARY KEY,
    username        VARCHAR(64)     NOT NULL UNIQUE,
    hashed_password VARCHAR(256)    NOT NULL,
    is_active       BOOLEAN         NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

COMMENT ON TABLE  users IS '用户表 - 存储系统登录用户账户信息';
COMMENT ON COLUMN users.id              IS '用户 ID，自增主键';
COMMENT ON COLUMN users.username        IS '用户名，唯一键，用于登录';
COMMENT ON COLUMN users.hashed_password IS '密码哈希值，存储 bcrypt 等算法加密后的密码';
COMMENT ON COLUMN users.is_active       IS '是否激活，默认 true，可用于禁用账户';
COMMENT ON COLUMN users.created_at      IS '账户创建时间';
