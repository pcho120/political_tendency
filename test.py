import requests

r = requests.get("https://www.kirkland.com/lawyers/c/cade-ashley")
print(len(r.text))
print("Education" in r.text)
