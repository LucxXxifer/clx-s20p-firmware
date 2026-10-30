-- Exercise the generated proxy with in-memory sockets and HTTP/session stubs.
local here = arg[0]:match("^(.*)/tests/") or "."
local file=assert(io.open(here .. "/istore_backend.lua"))
local candidate=file:read("*a");file:close()
local count=0
local function run(options)
  local output,status,closed,adapted,loaded="",nil,false,0,0
  local chunks={options.body or "UPSTREAM"}
  local line_index=0
  local lines={"HTTP/1.1 " .. (options.status or "200 OK"),"Content-Type: application/json","Content-Length: 8",""}
  if options.chunked then lines[3]="Transfer-Encoding: chunked" end
  local socket={send=function(_,s)return #s end,close=function()closed=true end,
    linesource=function()return function(body)
      if body then return options.chunked and "8\r\nUPSTREAM\r\n0\r\n\r\n" or "UPSTREAM" end
      line_index=line_index+1;return lines[line_index]
    end end,
    blocksource=function()return function()return nil end end}
  local envs={REQUEST_URI=options.uri or "/cgi-bin/luci/istore/u/network/status/",REQUEST_METHOD=options.method or "GET"}
  local http={getenv=function(k)return envs[k] end,getcookie=function()return options.auth and "sid" or nil end,
    context={request={message={env={}}}},source=function()return function()end end,
    status=function(s)status=s end,header=function()end,write=function(s)if s then output=output..s end;return 1 end}
  local ltn12={source={},sink={},pump={}}
  ltn12.source.string=function(s)local used=false;return function()if not used then used=true;return s end end end
  ltn12.source.cat=function(...)
    local sources={...};local i=1
    return function()while sources[i] do local value,err=sources[i]();if value then return value,err end;if err then return nil,err end;i=i+1 end end
  end
  ltn12.sink.table=function(t)return function(value)if value then t[#t+1]=value end;return 1 end end
  ltn12.pump.all=function(source,sink)
    while true do local value,err=source();if err then return nil,err end;if not value then sink(nil);return 1 end;sink(value) end
  end
  local fake_adapter={production_env=function()return {} end,transform=function(kind,body)
    adapted=adapted+1
    if options.error then return nil,"explicit measurement failure" end
    return options.preserve and body or "ADAPTED-"..kind
  end}
  local dependencies={ ["luci.http"]=http,["nixio"]={connect=function()return socket end},["luci.ltn12"]=ltn12,
    ["table"]=table,["luci.util"]={ubus=function()return {values={token="token"}} end},
    ["luci.model.s20p_dashboard"]=fake_adapter,["luci.model.s20p_ports"]=fake_adapter,["luci.jsonc"]={stringify=function()return "EXPLICIT-ERROR" end}}
  local sandbox=setmetatable({module=function()end,package={seeall={}},
    require=function(name)if name=="luci.model.s20p_dashboard" or name=="luci.model.s20p_ports" then loaded=loaded+1 end;return assert(dependencies[name],name) end}, {__index=_G})
  local compiled, compile_error
  if _VERSION == "Lua 5.1" then
    compiled, compile_error = loadstring(candidate, "controller")
    if compiled then setfenv(compiled, sandbox) end
  else
    compiled, compile_error = load(candidate, "controller", "t", sandbox)
  end
  assert(compiled, compile_error)()
  sandbox.istore_backend()
  assert(closed,"socket closed")
  return output,status,adapted,loaded
end
local function test(name,fn)fn();count=count+1;print("ok "..count.." - "..name)end
test("authenticated status, statistics and ports use their isolated adapters",function()
  for _,kind in ipairs({"status","statistics","ports"}) do
    local body,status,n=run({auth=true,uri=kind=="ports" and "/cgi-bin/luci/istore/network/port/list/" or "/cgi-bin/luci/istore/u/network/"..kind.."/"})
    assert(body=="ADAPTED-"..kind and status==200 and n==1)
  end
end)
test("unauth, non-GET, failed HTTP, unknown API and query remain byte-identical",function()
  for _,options in ipairs({{}, {auth=true,method="POST"},{auth=true,status="403 Forbidden"},
    {auth=true,uri="/cgi-bin/luci/istore/guide/dns-config/"},
    {auth=true,uri="/cgi-bin/luci/istore/u/network/status/?x=1"}}) do
    local body,_,n,loaded=run(options);assert(body=="UPSTREAM" and n==0 and loaded==0)
  end
end)
test("port endpoint preserves authentication, method, status and exact URI boundaries",function()
  local uri="/cgi-bin/luci/istore/network/port/list/"
  for _,options in ipairs({{uri=uri},{auth=true,uri=uri,method="POST"},
    {auth=true,uri=uri,status="403 Forbidden"},{auth=true,uri=uri.."?x=1"}})do
    local body,_,n,loaded=run(options)
    assert(body=="UPSTREAM" and n==0 and loaded==0)
  end
  local body,status,n=run({auth=true,uri=uri,chunked=true})
  assert(body=="ADAPTED-ports" and status==200 and n==1)
end)
test("legitimate backend JSON errors can pass original body bytes",function()
  local body,status,n=run({auth=true,preserve=true});assert(body=="UPSTREAM" and status==200 and n==1)
end)
test("adapter failure returns explicit 503 with no fabricated stats",function()
  local body,status,n=run({auth=true,error=true});assert(body=="EXPLICIT-ERROR" and status==503 and n==1)
end)
test("chunked successful body is dechunked before adaptation",function()
  local body,status,n=run({auth=true,chunked=true});assert(body=="ADAPTED-status" and status==200 and n==1)
end)
print("passed "..count.." proxy controller tests")
