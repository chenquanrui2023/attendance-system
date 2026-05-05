"""打卡系统 - 启动入口
使用: python test.py
然后在浏览器打开 http://127.0.0.1:5000
"""
from app import app

if __name__ == '__main__':
    print('=' * 50)
    print('  打卡系统已启动！')
    print('  请在浏览器中打开: http://127.0.0.1:5000')
    print('=' * 50)
    app.run(host='127.0.0.1', debug=True, port=5000)
