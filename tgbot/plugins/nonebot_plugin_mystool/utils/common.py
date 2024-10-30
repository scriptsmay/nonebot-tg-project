import hashlib
import io
import json
import os
import random
import string
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import (Dict, Literal,
                    Union, Optional, Tuple, Iterable, List)
from urllib.parse import urlencode

import httpx
import nonebot.log
import nonebot.plugin
import tenacity

try:
    from loguru import Logger
except ImportError:
    Logger = None
    pass

from nonebot import Adapter, Bot

from nonebot_plugin_saa import MessageSegmentFactory, Text, AggregatedMessageFactory, TargetQQPrivate, \
    TargetQQGuildDirect, enable_auto_select_bot

from nonebot.adapters.onebot.v11 import MessageEvent as OneBotV11MessageEvent, PrivateMessageEvent, GroupMessageEvent, \
    Adapter as OneBotV11Adapter, Bot as OneBotV11Bot
from nonebot.adapters.qq import DirectMessageCreateEvent, MessageCreateEvent, \
    Adapter as QQGuildAdapter, Bot as QQGuildBot, MessageEvent
from nonebot.log import logger
from nonebot.log import logger
from qrcode import QRCode

from ..model import GeetestResult, PluginDataManager, Preference, plugin_config, plugin_env, UserData

__all__ = ["GeneralMessageEvent", "GeneralPrivateMessageEvent", "GeneralGroupMessageEvent", "CommandBegin",
           "get_last_command_sep", "COMMAND_BEGIN", "set_logger", "logger", "PLUGIN", "custom_attempt_times",
           "get_async_retry", "generate_device_id", "cookie_str_to_dict", "cookie_dict_to_str", "generate_ds",
           "get_validate", "generate_seed_id", "generate_fp_locally", "get_file", "blur_phone", "generate_qr_img",
           "send_private_msg", "get_unique_users", "get_all_bind", "read_blacklist", "read_whitelist",
           "read_admin_list"]

# 启用 nonebot-plugin-send-anything-anywhere 的自动选择 Bot 功能
enable_auto_select_bot()

GeneralMessageEvent = OneBotV11MessageEvent, MessageCreateEvent, DirectMessageCreateEvent, MessageEvent
"""消息事件类型"""
GeneralPrivateMessageEvent = PrivateMessageEvent, DirectMessageCreateEvent
"""私聊消息事件类型"""
GeneralGroupMessageEvent = GroupMessageEvent, MessageCreateEvent
"""群聊消息事件类型"""


class CommandBegin:
    """
    命令开头字段
    （包括例如'/'和插件命令起始字段例如'mystool'）
    已重写__str__方法
    """
    string = ""
    '''命令开头字段（包括例如'/'和插件命令起始字段例如'mystool'）'''

    @classmethod
    def set_command_begin(cls):
        """
        机器人启动时设置命令开头字段
        """
        if nonebot.get_driver().config.command_start:
            cls.string = list(nonebot.get_driver().config.command_start)[0] + plugin_config.preference.command_start
        else:
            cls.string = plugin_config.preference.command_start

    @classmethod
    def __str__(cls):
        return cls.string


def get_last_command_sep():
    """
    获取第最后一个命令分隔符
    """
    if nonebot.get_driver().config.command_sep:
        return list(nonebot.get_driver().config.command_sep)[-1]


COMMAND_BEGIN = CommandBegin()
'''命令开头字段（包括例如'/'和插件命令起始字段例如'mystool'）'''


def set_logger(_logger: "Logger"):
    """
    给日志记录器对象增加输出到文件的Handler
    """
    # 根据"name"筛选日志，如果在 plugins 目录加载，则通过 LOG_HEAD 识别
    # 如果不是插件输出的日志，但是与插件有关，则也进行保存
    logger.add(
        plugin_config.preference.log_path,
        diagnose=False,
        format=nonebot.log.default_format,
        rotation=plugin_config.preference.log_rotation,
        filter=lambda x: x["name"] == plugin_config.preference.plugin_name or (
                plugin_config.preference.log_head != "" and x["message"].find(plugin_config.preference.log_head) == 0
        ) or x["message"].find(f"plugins.{plugin_config.preference.plugin_name}") != -1
    )

    return logger


logger = set_logger(logger)
"""本插件所用日志记录器对象（包含输出到文件）"""

PLUGIN = nonebot.plugin.get_plugin(plugin_config.preference.plugin_name)
'''本插件数据'''

if not PLUGIN:
    logger.warning(
        "插件数据(Plugin)获取失败，如果插件是从本地加载的，需要修改配置文件中 PLUGIN_NAME 为插件目录，否则将导致无法获取插件帮助信息等")


def custom_attempt_times(retry: bool):
    """
    自定义的重试机制停止条件\n
    根据是否要重试的bool值，给出相应的`tenacity.stop_after_attempt`对象

    :param retry True - 重试次数达到配置中 MAX_RETRY_TIMES 时停止; False - 执行次数达到1时停止，即不进行重试
    """
    if retry:
        return tenacity.stop_after_attempt(plugin_config.preference.max_retry_times + 1)
    else:
        return tenacity.stop_after_attempt(1)


def get_async_retry(retry: bool):
    """
    获取异步重试装饰器

    :param retry: True - 重试次数达到偏好设置中 max_retry_times 时停止; False - 执行次数达到1时停止，即不进行重试
    """
    return tenacity.AsyncRetrying(
        stop=custom_attempt_times(retry),
        retry=tenacity.retry_if_exception_type(BaseException),
        wait=tenacity.wait_fixed(plugin_config.preference.retry_interval),
    )


def generate_device_id() -> str:
    """
    生成随机的x-rpc-device_id
    """
    return str(uuid.uuid4()).upper()


def cookie_str_to_dict(cookie_str: str) -> Dict[str, str]:
    """
    将字符串Cookie转换为字典Cookie
    """
    cookie_str = cookie_str.replace(" ", "")
    # Cookie末尾缺少 ; 的情况
    if cookie_str[-1] != ";":
        cookie_str += ";"

    cookie_dict = {}
    start = 0
    while start != len(cookie_str):
        mid = cookie_str.find("=", start)
        end = cookie_str.find(";", mid)
        cookie_dict.setdefault(cookie_str[start:mid], cookie_str[mid + 1:end])
        start = end + 1
    return cookie_dict


def cookie_dict_to_str(cookie_dict: Dict[str, str]) -> str:
    """
    将字符串Cookie转换为字典Cookie
    """
    cookie_str = ""
    for key in cookie_dict:
        cookie_str += (key + "=" + cookie_dict[key] + ";")
    return cookie_str


def generate_ds(data: Union[str, dict, list, None] = None, params: Union[str, dict, None] = None,
                platform: Literal["ios", "android"] = "ios", salt: Optional[str] = None):
    """
    获取Headers中所需DS

    :param data: 可选，网络请求中需要发送的数据
    :param params: 可选，URL参数
    :param platform: 可选，平台，ios或android
    :param salt: 可选，自定义salt
    """
    if data is None and params is None or \
            salt is not None and salt != plugin_env.salt_config.SALT_PROD:
        if platform == "ios":
            salt = salt or plugin_env.salt_config.SALT_IOS
        else:
            salt = salt or plugin_env.salt_config.SALT_ANDROID
        t = str(int(time.time()))
        a = "".join(random.sample(
            string.ascii_lowercase + string.digits, 6))
        re = hashlib.md5(
            f"salt={salt}&t={t}&r={a}".encode()).hexdigest()
        return f"{t},{a},{re}"
    else:
        if params:
            salt = plugin_env.salt_config.SALT_PARAMS if not salt else salt
        else:
            salt = plugin_env.salt_config.SALT_DATA if not salt else salt

        if not data:
            if salt == plugin_env.salt_config.SALT_PROD:
                data = {}
            else:
                data = ""
        if not params:
            params = ""

        if not isinstance(data, str):
            data = json.dumps(data)
        if not isinstance(params, str):
            params = urlencode(params)

        t = str(int(time.time()))
        r = str(random.randint(100000, 200000))
        c = hashlib.md5(
            f"salt={salt}&t={t}&r={r}&b={data}&q={params}".encode()).hexdigest()
        return f"{t},{r},{c}"


async def get_validate(user: UserData, gt: str = None, challenge: str = None, retry: bool = True):
    """
    使用打码平台获取人机验证validate

    :param user: 用户数据对象
    :param gt: 验证码gt
    :param challenge: challenge
    :param retry: 是否允许重试
    :return: 如果配置了平台URL，且 gt, challenge 不为空，返回 GeetestResult
    """
    if not plugin_config.preference.global_geetest:
        if not (gt and challenge) or not user.geetest_url:
            return GeetestResult("", "")
        geetest_url = user.geetest_url
        params = {"gt": gt, "challenge": challenge}
        params.update(user.geetest_params or {})
    else:
        if not (gt and challenge) or not plugin_config.preference.geetest_url:
            return GeetestResult("", "")
        geetest_url = plugin_config.preference.geetest_url
        params = {"gt": gt, "challenge": challenge}
        params.update(plugin_config.preference.geetest_params or {})
    content = deepcopy(plugin_config.preference.geetest_json or Preference().geetest_json)
    for key, value in content.items():
        if isinstance(value, str):
            content[key] = value.format(gt=gt, challenge=challenge)
    debug_log = {"geetest_url": geetest_url, "params": params, "content": content}
    logger.debug(f"{plugin_config.preference.log_head}get_validate: {debug_log}")
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                geetest_url,
                params=params,
                json=content,
                timeout=60
            )
        geetest_data = res.json()
        logger.debug(f"{plugin_config.preference.log_head}人机验证结果：{geetest_data}")
        validate = geetest_data['data']['validate']
        seccode = geetest_data['data'].get('seccode') or f"{validate}|jordan"
        return GeetestResult(validate=validate, seccode=seccode)
    except Exception:
        logger.exception(f"{plugin_config.preference.log_head}获取人机验证validate失败")


def generate_seed_id(length: int = 8) -> str:
    """
    生成随机的 seed_id（即长度为8的十六进制数）

    :param length: 16进制数长度
    """
    max_num = int("FF" * length, 16)
    return hex(random.randint(0, max_num))[2:]


def generate_fp_locally(length: int = 13):
    """
    于本地生成 device_fp

    :param length: device_fp 长度
    """
    characters = string.digits + "abcdef"
    return ''.join(random.choices(characters, k=length))


async def get_file(url: str, retry: bool = True):
    """
    下载文件

    :param url: 文件URL
    :param retry: 是否允许重试
    :return: 文件数据，若下载失败则返回 ``None``
    """
    try:
        async for attempt in get_async_retry(retry):
            with attempt:
                async with httpx.AsyncClient() as client:
                    res = await client.get(url, timeout=plugin_config.preference.timeout, follow_redirects=True)
                return res.content
    except tenacity.RetryError:
        logger.exception(f"{plugin_config.preference.log_head}下载文件 - {url} 失败")
        return None


def blur_phone(phone: Union[str, int]) -> str:
    """
    模糊手机号

    :param phone: 手机号
    :return: 模糊后的手机号
    """
    if isinstance(phone, int):
        phone = str(phone)
    return f"☎️{phone[-4:]}"


def generate_qr_img(data: str):
    """
    生成二维码图片

    :param data: 二维码数据

    >>> b = generate_qr_img("https://github.com/Ljzd-PRO/nonebot-plugin-mystool")
    >>> isinstance(b, bytes)
    """
    qr_code = QRCode(border=2)
    qr_code.add_data(data)
    qr_code.make()
    image = qr_code.make_image()
    image_bytes = io.BytesIO()
    image.save(image_bytes)
    return image_bytes.getvalue()


async def send_private_msg(
        user_id: str,
        message: Union[str, MessageSegmentFactory, AggregatedMessageFactory],
        use: Union[Bot, Adapter] = None,
        guild_id: int = None
) -> Tuple[bool, Optional[Exception]]:
    """
    主动发送私信消息

    :param user_id: 目标用户ID
    :param message: 消息内容
    :param use: 使用的Bot或Adapter，为None则使用所有Bot
    :param guild_id: 用户所在频道ID，为None则从用户数据中获取
    :return: (是否发送成功, ActionFailed Exception)
    """
    user_id_int = int(user_id)
    if isinstance(message, str):
        message = Text(message)

    # 整合符合条件的 Bot 对象
    if isinstance(use, (OneBotV11Bot, QQGuildBot)):
        bots = [use]
    elif isinstance(use, (OneBotV11Adapter, QQGuildAdapter)):
        bots = use.bots.values()
    else:
        bots = nonebot.get_bots().values()

    for bot in bots:
        try:
            # 获取 PlatformTarget 对象
            if isinstance(bot, OneBotV11Bot):
                target = TargetQQPrivate(user_id=user_id_int)
                logger.info(
                    f"{plugin_config.preference.log_head}向用户 {user_id} 发送 QQ 聊天私信 user_id: {user_id_int}")
            else:
                if guild_id is None:
                    if user := PluginDataManager.plugin_data.users.get(user_id):
                        if not (guild_id := user.qq_guild.get(user_id)):
                            logger.error(f"{plugin_config.preference.log_head}用户 {user_id} 数据中没有任何频道ID")
                            return False, None
                    else:
                        logger.error(
                            f"{plugin_config.preference.log_head}用户数据中不存在用户 {user_id}，无法获取频道ID")
                        return False, None
                target = TargetQQGuildDirect(recipient_id=user_id_int, source_guild_id=guild_id)
                logger.info(f"{plugin_config.preference.log_head}向用户 {user_id} 发送 QQ 频道私信"
                            f" recipient_id: {user_id_int}, source_guild_id: {guild_id}")

            await message.send_to(target=target, bot=bot)
        except Exception as e:
            return False, e
        else:
            return True, None


def get_unique_users() -> Iterable[Tuple[str, UserData]]:
    """
    获取 不包含绑定用户数据 的所有用户数据以及对应的ID，即不会出现值重复项

    :return: dict_items[用户ID, 用户数据]
    """
    return filter(lambda x: x[0] not in PluginDataManager.plugin_data.user_bind,
                  PluginDataManager.plugin_data.users.items())


def get_all_bind(user_id: str) -> Iterable[str]:
    """
    获取绑定该用户的所有用户ID

    :return: 绑定该用户的所有用户ID
    """
    user_id_filter = filter(lambda x: PluginDataManager.plugin_data.user_bind.get(x) == user_id,
                            PluginDataManager.plugin_data.user_bind)
    return user_id_filter


def _read_user_list(path: Path) -> List[str]:
    """
    从TEXT读取用户名单

    :return: 名单中的所有用户ID
    """
    if not path:
        return []
    if os.path.isfile(path):
        with open(path, "r", encoding=plugin_config.preference.encoding) as f:
            lines = f.readlines()
        lines = map(lambda x: x.strip(), lines)
        line_filter = filter(lambda x: x and x != "\n", lines)
        return list(line_filter)
    else:
        logger.error(f"{plugin_config.preference.log_head}黑/白名单文件 {path} 不存在")
        return []


def read_blacklist() -> List[str]:
    """
    读取黑名单

    :return: 黑名单中的所有用户ID
    """
    return _read_user_list(plugin_config.preference.blacklist_path) if plugin_config.preference.enable_blacklist else []


def read_whitelist() -> List[str]:
    """
    读取白名单

    :return: 白名单中的所有用户ID
    """
    return _read_user_list(plugin_config.preference.whitelist_path) if plugin_config.preference.enable_whitelist else []


def read_admin_list() -> List[str]:
    """
    读取白名单

    :return: 管理员名单中的所有用户ID
    """
    return _read_user_list(
        plugin_config.preference.admin_list_path) if plugin_config.preference.enable_admin_list else []
