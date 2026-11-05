from aip import AipFace
from aip import AipSpeech

import base64

from pypinyin.core import lazy_pinyin

from script.wenzhuanpinyin import Style


class BaiDuFace:
    # 人脸识别初始化
    def __init__(self, APP_ID='123018776', API_KEY='ZhyP2q1zsF5AlptPZvPTAFpY',
                 SECRET_KEY='wnvdltgcfH4eXNUd0w5hI8wqseqILRS8'):
        self.APP_ID = APP_ID
        self.API_KEY = API_KEY
        self.SECRET_KEY = SECRET_KEY
        self.client = AipFace(self.APP_ID, self.API_KEY, self.SECRET_KEY)

    # 注册添加人脸
    def add_user(self, file_obj, userId):
        image = base64.b64encode(file_obj.read()).decode('utf-8')
        imageType = "BASE64"
        groupId = "group1"

        options = {}
        options["user_info"] = "user's info"
        options["quality_control"] = "NORMAL"
        options["liveness_control"] = "LOW"
        options["action_type"] = "REPLACE"

        res = self.client.addUser(image, imageType, groupId, userId, options)
        return res

    # 删除人脸
    def delete_user(self, userId, face_token):
        groupId = "group1"
        res = self.client.faceDelete(userId, groupId, face_token)
        return res

    # 查询人脸
    def search_user(self, file_obj):
        image = base64.b64encode(file_obj.read()).decode('utf-8')

        imageType = "BASE64"

        groupIdList = "group1"

        """ 调用人脸搜索 """
        res = self.client.search(image, imageType, groupIdList);
        return res

    # 人名转拼英
    def name_to_pinyin(self, text):
        style = Style.TONE3
        name_list = lazy_pinyin(text, style=style)
        return "".join(name_list)


class BaiDuVoice:
    def __init__(self, APP_ID='123155212', API_KEY='AUs37F3NykDhGNKquZo50bEH',
                 SECRET_KEY='GgIDozI1fDPVTaaKbTUn6ercE28iTc7F'):
        self.APP_ID = APP_ID
        self.API_KEY = API_KEY
        self.SECRET_KEY = SECRET_KEY
        self.client = AipSpeech(self.APP_ID, self.API_KEY, self.SECRET_KEY)

    def speech(self, voice_object):
        res = self.client.asr(voice_object.read(), 'pcm', 16000, {
            'dev_pid': 1537,
        })
        return res
