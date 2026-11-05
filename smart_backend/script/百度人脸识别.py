from aip import AipFace
from aip import AipSpeech

import base64


class BaiDuFace:
    def __init__(self, APP_ID='123018776', API_KEY='ZhyP2q1zsF5AlptPZvPTAFpY',
                 SECRET_KEY='wnvdltgcfH4eXNUd0w5hI8wqseqILRS8'):
        self.APP_ID = APP_ID
        self.API_KEY = API_KEY
        self.SECRET_KEY = SECRET_KEY
        self.client = AipFace(self.APP_ID, self.API_KEY, self.SECRET_KEY)

    def add_user(self):
        image = base64.b64encode(open('2.jpg', 'rb').read()).decode('utf-8')
        imageType = "BASE64"
        groupId = "group1"
        userId = "user1"

        options = {}
        options["user_info"] = "user's info"
        options["quality_control"] = "NORMAL"
        options["liveness_control"] = "LOW"
        options["action_type"] = "REPLACE"

        res = self.client.addUser(image, imageType, groupId, userId, options)
        return res

    def delete_user(self):
        userId = "user1"
        groupId = "group1"
        faceToken = "176ad6faba77547109f78bcffa4b7726"
        res = self.client.faceDelete(userId, groupId, faceToken)
        return res

    def search_user(self):
        image = "取决于image_type参数，传入BASE64字符串或URL字符串或FACE_TOKEN字符串"

        imageType = "BASE64"

        groupIdList = "3,2"

        """ 调用人脸搜索 """
        res = self.client.search(image, imageType, groupIdList);
        return res


# 语音识别

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


if __name__ == '__main__':
    ai = BaiDuFace()
    # res = ai.add_user()
    res = ai.delete_user()
    print(res)
