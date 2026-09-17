# Workspace and dataset discovery

Use the server root URL, including its deployment prefix but excluding `/api`:

```python
from gfslib import GFClient

server = GFClient("URL")
server.set_api_key("your_api_key")

for workspace in server.workspaces():
    print(workspace["uuid"], workspace["name"])

workspace_id = "your-workspace-id"
info = server.workspace(workspace_id)
datasets = server.datasets(workspace_id)
datasources = server.datasources(workspace_id)

storage = server.storage(workspace_id)
storage.upload_text("hello.txt", "Hello").raise_for_status()
```

Discovery methods return the server's JSON records unchanged. Dataset records
include attributes and datasource information where provided by GF. HTTP errors
and malformed JSON raise exceptions. Availability depends on server permissions.

`storage()` creates a `StorageServices` client with the current API key and
timeout. Later key changes on `GFClient` do not alter already-created clients.
The existing direct `StorageServices(service_url)` constructor remains supported.
