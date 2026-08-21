# Deterministic counterpart to the "Successful creation of a category" Scenario in
# ../ApiTestingNew.feature. Same contract, same server/credentials from test_data — this
# file just asserts it without an LLM in the loop. Login is repeated here rather than
# shared across files because Hurl has no cross-file setup and each Gherkin Scenario
# already re-logs-in for the same reason (see ../ApiTestingNew.feature's Background).

POST {{server}}auth/jwt/login
Content-Type: application/x-www-form-urlencoded
[FormParams]
username: {{username}}
password: {{password}}

HTTP 200
[Captures]
token: jsonpath "$.access_token"

POST {{server}}categories
Authorization: Bearer {{token}}
Content-Type: application/json
{
    "name": "hurl-category-{{run_id}}",
    "description": "created by run_hurl_suite"
}

HTTP 200
[Asserts]
jsonpath "$.name" == "hurl-category-{{run_id}}"
jsonpath "$.description" == "created by run_hurl_suite"
jsonpath "$.id" exists
