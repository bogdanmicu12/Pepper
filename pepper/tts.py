from naoqi import ALProxy
tts = ALProxy("ALTextToSpeech", "192.168.1.35", 9559)
tts.say("Hello, world!")