# Deterministic counterpart to the "Creation of a category without name" Scenario in
# ../ApiTestingNew.feature.

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
    "description": "missing the required name field"
}

HTTP 422
[Asserts]
jsonpath "$.detail[0].loc" count == 2
jsonpath "$.detail[0].loc[0]" == "body"
jsonpath "$.detail[0].loc[1]" == "name"
jsonpath "$.detail[0].type" == "missing"
