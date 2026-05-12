import io
with io.open('logs/bot.log', 'r', encoding='utf-8') as f:
    lines = f.readlines()
    for line in lines[-50:]:
        print(line.encode('ascii', 'replace').decode('ascii'), end='')
