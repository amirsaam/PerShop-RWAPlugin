with open('rwa_plugin_pershop/static/app.js', 'r') as f:
    content = f.read()
# Put health + quick actions in 2-col grid, close grid after quick actions
# Replace the standalone health card opening with grid opening containing health
content = content.replace(
    "+ '<div class=\"' + CARD + '\"><div class=\"flex items-center justify-between mb-3\"><h2 class=\"' + CARD_T + '\">Shop health</h2>"
    , "+ '<div class=\"grid gap-4 sm:grid-cols-2\">' + '<div class=\"' + CARD + '\"><div class=\"flex items-center justify-between mb-3\"><h2 class=\"' + CARD_T + '\">Shop health</h2>")

# After quick actions close, add grid close, then full-width recent activity
content = content.replace(
    "+ '</div></div>'\n        + '<div class=\"' + CARD + '\"><h2 class=\"' + CARD_T + '\">Recent activity</h2>\n        + '<p class=\"text-xs text-dark-300 mb-3\">Newest first.",
    "+ '</div></div>'\n        + '</div>'\n        + '<div class=\"' + CARD + ' mt-4\"><h2 class=\"' + CARD_T + '\">Recent activity</h2>\n        + '<div class=\"overflow-x-auto\"><table class=\"table text-sm\"><thead><tr><th>Time</th><th>Kind</th><th>Detail</th></tr></thead><tbody id=\"event-table\"></tbody></table></div></div>"
)
with open('rwa_plugin_pershop/static/app.js', 'w') as f:
    f.write(content)
print('done')
