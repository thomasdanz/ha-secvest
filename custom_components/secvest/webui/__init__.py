"""Reading the panel's web interface (#56).

A different interface from the REST API: form login, session cookie, XML
data. Kept apart from the API client, without Home Assistant imports, and
sent through the same transport (one connection, one queue). See
secvest-api's openapi-webui.yaml.
"""
