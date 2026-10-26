# One ::error:: line per failed assertion (or provider error: failureReason 2) in a promptfoo results file.
# Workflow-command escaping: % \r \n in the message; also : and , in properties.
def msg: tostring | gsub("%"; "%25") | gsub("\r"; "%0D") | gsub("\n"; "%0A");
def prop: msg | gsub(":"; "%3A") | gsub(","; "%2C");
def name: if .type == "javascript" then (.value | tostring | split("/") | last)
          else "\(.type): \(.value // "" | tostring | split("\n") | map(select(test("\\S"))) | first // "" | .[0:50])" end;
.results.results[]
| if .failureReason == 2 then "::error title=\($attempt) provider error::\(.error | .[0:900] | msg)"
  else (.gradingResult.componentResults // [])[]
       | select(.pass | not)
       | "::error title=\("\($attempt) \(.assertion | name)" | prop)::\(.reason // "" | .[0:900] | msg)"
  end
