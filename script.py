from dotenv import load_dotenv
import os
import base64
import requests
from collections import defaultdict
from datetime import datetime
import csv

load_dotenv() 

# === Variables d’environnement Azure DevOps ===
AZDO_ORG = os.getenv("AZDO_ORG")
AZDO_PROJECT = os.getenv("AZDO_PROJECT")
QUERY_ID = os.getenv("QUERY_ID")
AZDO_PAT = os.getenv("AZDO_PAT")
SEVENPACE_PAT = os.getenv("SEVENPACE_PAT")
NOM_ACTIVITE_DEV = os.getenv("NOM_ACTIVITE_DEV")
NOM_ACTIVITE_TEST = os.getenv("NOM_ACTIVITE_TEST")

# === Headers ===
basic_token = base64.b64encode(f":{AZDO_PAT}".encode()).decode()
azdo_headers = {
    "Content-Type": "application/json",
    "Authorization": f"Basic {basic_token}"
}
sevenpace_headers = {
    "Authorization": f"Bearer {SEVENPACE_PAT}",
    "Accept": "application/json"
}

# === Étape 1 : Requête AzDO WIQL ===
url_query = f"https://dev.azure.com/{AZDO_ORG}/{AZDO_PROJECT}/_apis/wit/wiql/{QUERY_ID}?api-version=7.0"
resp = requests.get(url_query, headers=azdo_headers).json()
if "workItems" not in resp or not resp["workItems"]:
    print("⚠️ Aucun ticket trouvé dans la requête.")
    exit(1)

ids = [str(item["id"]) for item in resp["workItems"]]

# === Étape 2 : Détails des tickets et enfants ===
parent_child_map = defaultdict(list)
tickets = []
child_ids = []

for ticket_id in ids:
    url = f"https://dev.azure.com/{AZDO_ORG}/_apis/wit/workitems/{ticket_id}?$expand=relations&api-version=7.0"
    r = requests.get(url, headers=azdo_headers).json()
    fields = r.get("fields", {})
    relations = r.get("relations", [])

    ticket = {
        "ID": ticket_id,
        "Title": fields.get("System.Title", ""),
        "State": fields.get("System.State", ""),
        "Type": fields.get("System.WorkItemType", ""),
        "Priority": fields.get("Microsoft.VSTS.Common.Priority", ""),
        "EstimatedDevTime": fields.get("Custom.EstimatedDevelopmentTime", ""),
        "EstimatedTestTime": fields.get("Custom.EstimatedTestingTime", "")
    }
    tickets.append(ticket)

    for rel in relations:
        if rel.get("rel") == "System.LinkTypes.Hierarchy-Forward":
            child_id = rel.get("url", "").split("/")[-1]
            parent_child_map[ticket_id].append(child_id)
            child_ids.append(child_id)

# === Étape 3 : Récupération des worklogs ===
all_ids = ids + child_ids
url_odata = "https://cegid.timehub.7pace.com/api/odata/v3.2/workLogsOnly/$query"
payload = f"$filter=WorkItemId in ({','.join(all_ids)})"

headers_post = {
    "Authorization": f"Bearer {SEVENPACE_PAT}",
    "Content-Type": "text/plain",
    "Accept": "application/json"
}

print("🔎 Appel 7pace OData…")
response = requests.post(url_odata, headers=headers_post, data=payload)

if response.status_code != 200:
    print("❌ Erreur HTTP :", response.status_code)
    print(response.text)
    exit(1)

try:
    r = response.json()
except Exception as e:
    print("❌ Erreur JSON :", e)
    exit(1)

worklogs = r.get("value", [])
print(f"✅ {len(worklogs)} worklogs récupérés")

# === Agrégation des activités ===
child_to_parent = {cid: pid for pid, children in parent_child_map.items() for cid in children}
time_data = defaultdict(lambda: {"TempsTotal": 0, "TempsDev": 0, "TempsTest": 0, "AutresActivités": 0})

for t in tickets:
    time_data[t["ID"]]  # init

for log in worklogs:
    work_id = str(log["WorkItemId"])
    duration = log.get("PeriodLength", 0)
    activity = (
        log.get("Activity", {}).get("Name")
        or log.get("ActivityType", {}).get("Name")
        or ""
    )
    target_id = child_to_parent.get(work_id, work_id)
    time_data[target_id]["TempsTotal"] += duration
    if activity == NOM_ACTIVITE_DEV:
        time_data[target_id]["TempsDev"] += duration
    elif activity == NOM_ACTIVITE_TEST:
        time_data[target_id]["TempsTest"] += duration
    else:
        time_data[target_id]["AutresActivités"] += duration


# === Format HH:MM (forcé en texte) ===
def format_hhmm(secs):
    h = secs // 3600
    m = (secs % 3600) // 60
    return f"{h:02d}:{m:02d}"

# === Export CSV ===
rows = []
for t in tickets:
    tid = t["ID"]
    temps = time_data[tid]
    t.update({
        "TempsTotal": format_hhmm(temps["TempsTotal"]),
        "TempsDev": format_hhmm(temps["TempsDev"]),
        "TempsTest": format_hhmm(temps["TempsTest"]),
        "AutresActivités": format_hhmm(temps["AutresActivités"]),
    })
    rows.append(t)

filename = f"export_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
with open(filename, "w", newline='', encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys(), delimiter=";")
    writer.writeheader()
    writer.writerows(rows)

print(f"✅ Export terminé : {filename}")