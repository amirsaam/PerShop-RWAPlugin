with open('rwa_plugin_pershop/static/app.js', 'r') as f:
    content = f.read()
content = content.replace('id="events"', 'id="event-table"')
content = content.replace("q('events').innerHTML", "q('event-table').innerHTML")
with open('rwa_plugin_pershop/static/app.js', 'w') as f:
    f.write(content)
print('Fixed event ID')
