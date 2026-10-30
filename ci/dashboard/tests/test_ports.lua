local here=arg[0]:match("^(.*)/tests/") or "."
local adapter=assert(dofile(here.."/luci/model/s20p_ports.lua"))
local passed=0
local function test(name,fn)fn();passed=passed+1;print("ok "..passed.." - "..name)end
local function clone(value)
  if type(value)~="table" then return value end
  local result={};for key,item in pairs(value)do result[key]=clone(item)end;return result
end
local function fixture()
  local body={result={ports={{name="eth2",interfaceNames={"usb_uplink"},linkState="UNKNOWN",
    linkSpeed="2000 Mbit/s",duplex="half",rx_packets="52.4MiB/72359pkts",tx_packets="13.1MiB/54249pkts"},
    {name="lan2",linkState="DOWN"}}},foreign="preserved"}
  local state={present=true,up=true,carrier=true}
  local calls=0
  local env={decode=function(value)return value end,encode=function(value)return value end,
    device=function(name)assert(name=="eth2");calls=calls+1;return clone(state)end}
  return body,state,env,function()return calls end
end
test("UNKNOWN USB link with positive netifd evidence becomes connected without mutating input",function()
  local body,_,env,calls=fixture()
  local value=assert(adapter.transform("ports",body,env))
  assert(value.result.ports[1].linkState=="UP","positive carrier USB must be displayed connected")
  assert(body.result.ports[1].linkState=="UNKNOWN" and calls()==1)
  value.result.ports[1].linkState="UNKNOWN"
  local function same(a,b)
    if type(a)~="table" then return a==b end
    for key,item in pairs(a)do if not same(item,b[key])then return false end end
    for key in pairs(b)do if a[key]==nil then return false end end
    return true
  end
  assert(same(value,body),"only linkState may change; speed is device link metadata")
end)
test("absent, administratively down or carrier-down USB never becomes UP",function()
  for _,field in ipairs({"present","up","carrier"})do
    for _,bad in ipairs({false,0,"true"})do
      local body,state,env=fixture();state[field]=bad
      local value=assert(adapter.transform("ports",body,env))
      assert(value.result.ports[1].linkState=="UNKNOWN")
    end
    local body,state,env=fixture();state[field]=nil
    assert(adapter.transform("ports",body,env).result.ports[1].linkState=="UNKNOWN")
  end
end)
test("other names, logical interfaces and explicit UP or DOWN are byte-preserved",function()
  for _,change in ipairs({function(p)p.name="lan2" end,function(p)p.name="eth2; reboot" end,
    function(p)p.interfaceNames={"wan"}end,function(p)p.interfaceNames=nil end,
    function(p)p.linkState="DOWN"end,function(p)p.linkState="UP"end})do
    local body,_,env,calls=fixture();change(body.result.ports[1])
    assert(adapter.transform("ports",body,env)==body and calls()==0)
  end
end)
test("upstream JSON errors and malformed responses remain original without new probes",function()
  for _,body in ipairs({{error="denied",result={ports={}}},{success=403,result={ports={}}},
    {result={}},{result={ports="invalid"}},{result={ports={false,"bad"}}}})do
    local env={decode=function(value)return value end,device=function()error("must not probe")end}
    assert(adapter.transform("ports",body,env)==body)
  end
end)
test("ubus failure cannot invent a successful link",function()
  local body,_,env=fixture()
  env.device=function()return nil,"read failed"end
  assert(adapter.transform("ports",body,env)==body)
  env.device=function()error("ubus exception")end
  assert(adapter.transform("ports",body,env)==body)
end)
test("production probe uses the read-only structured ubus device API",function()
  local calls=0
  package.loaded["luci.jsonc"]={parse=function()end,stringify=function()end}
  package.loaded["luci.util"]={ubus=function(object,method,arguments)
    assert(object=="network.device" and method=="status" and arguments.name=="eth2")
    calls=calls+1;return {present=true,up=true,carrier=true}
  end}
  local env=adapter.production_env()
  assert(env.device("eth2").carrier and calls==1)
end)
print("passed "..passed.." USB port state tests")
