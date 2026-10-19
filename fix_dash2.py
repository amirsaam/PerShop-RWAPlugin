with open('rwa_plugin_pershop/static/app.js') as f:
    content = f.read()

old = "+ '<p id=\"sec\" class=\"' + HELP + ' mt-2\"></p></div>'\n        + '<div class=\"grid gap-4 sm:grid-cols-2\">'"
new = "+ '<p id=\"sec\" class=\"' + HELP + ' mt-2\"></p></div>\n        + '</div>'"
content = content.replace(old, new)
with open('rwa_plugin_pershop/static/app.js', 'w') as f:
    f.write(content)
print('Fixed')
