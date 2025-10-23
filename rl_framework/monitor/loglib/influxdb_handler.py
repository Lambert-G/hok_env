import os
import logging
from logging.handlers import QueueHandler, QueueListener
from queue import Queue
import subprocess
import configparser
import ast
import time
import influxdb
import numpy as np
import numbers
import collections.abc

def _to_builtin(obj):
    # numpy 标量
    if isinstance(obj, (np.generic,)):
        return obj.item()
    # 基本数值直接返回
    if isinstance(obj, numbers.Number) or isinstance(obj, (str, bool)) or obj is None:
        return obj
    # 映射类型
    if isinstance(obj, dict):
        return {k: _to_builtin(v) for k, v in obj.items()}
    # 可迭代（list/tuple等）
    if isinstance(obj, collections.abc.Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        return type(obj)(_to_builtin(v) for v in obj)
    # numpy 数组
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    # 其他非常规类型，转字符串以保证可写
    return str(obj)

class InfluxdbMonitorFilter(logging.Filter):
    def __init__(self):
        super().__init__()

    def filter(self, record):
        if isinstance(record.msg, dict):
            return super().filter(record)
        else:
            return False

class InfluxdbMonitorFormatter(logging.Formatter):
    def __init__(self):
        super().__init__()
        is_gpu = False
        res = subprocess.run(
            ["nvidia-smi  -L"], shell=True, encoding="utf-8", stdout=subprocess.PIPE
        )
        if res.returncode == 0 and res.stdout != "":
            is_gpu = True
        res = subprocess.run(
            ["hostname"], shell=True, encoding="utf-8", stdout=subprocess.PIPE
        )
        hostname = res.stdout.strip()
        self._json_body = {
            "measurement": "gpu_ip_info" if is_gpu else "cpu_ip_info",
            "tags": {
                "ip_port": hostname,
                "type": "gpu" if is_gpu else "cpu",
            },
        }

    def format(self, record):
        # 1) 直接读取 dict（避免 literal_eval）
        if isinstance(record.msg, dict):
            msg_dict = record.msg.copy()
        else:
            # 兜底：只有在不是 dict 的情况下才尝试解析
            msg_dict = ast.literal_eval(record.getMessage())

        msg_dict = _to_builtin(msg_dict)
        # Transfer str message to dict
        #msg_dict = ast.literal_eval(record.getMessage())
        self._json_body["fields"] = msg_dict
        return self._json_body

class ActorMetricsFormatter(InfluxdbMonitorFormatter):
    def __init__(self):
        super().__init__()
        # 覆盖 measurement：固定写到 actor_metrics
        self._json_body["measurement"] = "actor_metrics"

    def format(self, record):
        msg_dict = ast.literal_eval(record.getMessage())
        # 可选：把 role/actor_id 提升为 tag，查询更高效
        role = msg_dict.pop("role", None)
        actor_id = msg_dict.pop("actor_id", None)
        if role:
            self._json_body["tags"]["role"] = role
        if actor_id is not None:
            self._json_body["tags"]["actor_id"] = str(actor_id)
        self._json_body["fields"] = msg_dict
        return self._json_body

class InfluxdbMonitorHandlerInner(logging.Handler):
    def __init__(self, ip, port, database):
        super().__init__()
        self._ip = ip
        self._port = port
        self._database = database
        self._client = self._create_influxdb_client()

    def _create_influxdb_client(self):
        return influxdb.InfluxDBClient(
            host=self._ip,
            port=self._port,
            database=self._database,
            timeout=1,
        )

    def emit(self, record):
        for _ in range(2):
            try:
                msg = self.format(record)
                # === 新增：把目标和字段打印出来（最关键的一锤定音） ===
                meas = msg.get("measurement")
                fields = list(msg.get("fields", {}).keys())
                tags = msg.get("tags", {})
                print(f"[emit] to {self._ip}:{self._port} db={self._database} "
                      f"measurement={meas} tags={tags} fields={fields}")

                self._client.write_points([msg])
                break
            except Exception as e:  # pylint: disable=broad-except
                # recreate influxdb client and try again
                print("[emit] error:", e)
                self._client.close()
                self._client = self._create_influxdb_client()


class InfluxdbMonitorHandler(logging.Handler):
    def __init__(self, ip, port=None, database=None):
        super().__init__()
        self._config = self._get_config()
        self._queue = Queue(self._config.getint("queue_size"))
        self._queue_handler = QueueHandler(self._queue)
        if port is None:
            port = self._config.get("port")
        if database is None:
            database = self._config.get("database")
        self._handler = InfluxdbMonitorHandlerInner(ip, port, database)

        # Influxdb Formatter
        formatter = InfluxdbMonitorFormatter()
        self._handler.setFormatter(formatter)

        # Accept dict type log only. QueueHandler will format record message
        # to str, so add InfluxdbMonitorFilter to queue handler, not dict not enqueue
        filter = InfluxdbMonitorFilter()
        self._queue_handler.addFilter(filter)

        self._queue_listener = QueueListener(self._queue, self._handler)
        self._queue_listener.start()

    def _get_config(self):
        config = configparser.ConfigParser()
        file_path = os.path.dirname(os.path.realpath(__file__))
        config.read(os.path.join(file_path, "loglib.conf"))
        return config["influxdb_handler"]

    def emit(self, record):
        self._queue_handler.handle(record)


if __name__ == "__main__":
    logger = logging.get_logger("handler")
    logger.setLevel(logging.DEBUG)

    handler = InfluxdbMonitorHandler("localhost")
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(logging.FileHandler("file.log"))
    logger.debug("hello world")
    logger.info("hello world")
    data = {"A": 1, "B": 2}
    for _ in range(5):
        logger.debug(data)
        logger.info(data)
    time.sleep(1)
