local here = arg[0]:match("^(.*)/tests/") or "."
package.path = here .. "/?.lua;" .. package.path
local ok, adapter = pcall(require, "luci.model.s20p_dashboard")
assert(ok and type(adapter) == "table", "dashboard adapter module must exist")

local passed = 0
local unpack_values = table.unpack or unpack
local function equal(actual, expected, message)
  assert(actual == expected, (message or "value mismatch") .. ": " .. tostring(actual) .. " ~= " .. tostring(expected))
end
local function test(name, fn)
  fn()
  passed = passed + 1
  print("ok " .. passed .. " - " .. name)
end
local function clone(value)
  if type(value) ~= "table" then return value end
  local result = {}
  for k, v in pairs(value) do result[k] = clone(v) end
  return result
end
local function fixture()
  local e = {
    routes = {{dst="default", gateway="192.168.31.1", dev="br-lan", metric=0}},
    interfaces = {{interface="lan", up=true, l3_device="br-lan", proto="static", uptime=123,
      ["ipv4-address"]={{address="192.168.31.2", mask=24}},
      ["dns-server"]={"223.5.5.5", "119.29.29.29"}}},
    config = {lan={dns={"223.5.5.5", "119.29.29.29"}, peerdns="0"}},
    time=100, wall=1700000000, rx=1000, tx=2000, ifindex=7, boot="test-boot",
    locked=false, state=nil, slept=0, command_calls=0, reads=0,
  }
  local env = {
    routes=function() e.command_calls=e.command_calls+1; return clone(e.routes) end,
    interfaces=function() return clone(e.interfaces) end,
    config=function(name) return clone(e.config[name] or {}) end,
    boot=function() return e.boot end,
    ifindex=function() return e.ifindex end,
    counter=function(_, which) e.reads=e.reads+1; return e[which] end,
    monotonic=function() return e.time end,
    epoch=function() return e.wall end,
    sleep=function(seconds)
      equal(seconds, 0.1, "first sample wait")
      e.slept=e.slept+1
      e.time=e.time+seconds; e.wall=e.wall+seconds; e.rx=e.rx+100; e.tx=e.tx+200
    end,
    lock=function()
      if e.locked then return nil, "busy" end
      e.locked=true
      return function() e.locked=false end
    end,
    load=function() return clone(e.state) end,
    store=function(value) e.state=clone(value); return true end,
    decode=function(value) return value end,
    encode=function(value) return value end,
  }
  return e, env
end

test("only exact authenticated successful GET endpoints qualify", function()
  local status="/cgi-bin/luci/istore/u/network/status/"
  local stats="/cgi-bin/luci/istore/u/network/statistics/"
  equal(adapter.eligible(status,"GET",true,200), "status")
  equal(adapter.eligible(stats,"GET",true,200), "statistics")
  for _, request in ipairs({{status,"POST",true,200},{status,"GET",false,200},
    {status,"GET",true,403},{status .. "?x=1","GET",true,200},
    {"/cgi-bin/luci/istore/guide/dns-config/","GET",true,200}}) do
    equal(adapter.eligible(unpack_values(request)), nil)
  end
end)

test("status selects actual kernel default while preserving connectivity evidence", function()
  local e, env=fixture()
  local original={result={defaultInterface="wan",networkInfo="netSuccess",foreign="kept"}}
  local value, err=adapter.transform("status",original,env)
  assert(value and not err)
  equal(value.result.defaultInterface,"lan"); equal(value.result.proto,"static")
  equal(value.result.ipv4addr,"192.168.31.2")
  equal(value.result.dnsProto,"manual"); equal(value.result.dnsList[2],"119.29.29.29")
  equal(value.result.networkInfo,"netSuccess"); equal(value.result.foreign,"kept")
  equal(value.result.uptimeStamp,123)
  equal(value.result.dashboardAdapter.device,"br-lan")
  equal(value.result.dashboardAdapter.dnsReadOnly,true)
  equal(e.slept,0)
end)

test("minimum metric wins and DHCP reports runtime DNS", function()
  local e,env=fixture()
  e.routes[2]={dst="default",gateway="192.168.8.1",dev="usb0",metric=5}
  e.routes[1].metric=20
  e.interfaces[2]={interface="huawei",up=true,l3_device="usb0",proto="dhcp",uptime=30,
    ["ipv4-address"]={{address="192.168.8.153",mask=24}},["dns-server"]={"192.168.8.1"}}
  e.config.huawei={}
  local value=assert(adapter.transform("status",{result={}},env))
  equal(value.result.defaultInterface,"huawei");equal(value.result.dnsProto,"auto")
  equal(value.result.dnsList[1],"192.168.8.1")
end)

test("missing default or equal-cost ambiguity returns explicit error", function()
  local e,env=fixture()
  e.routes={}
  local value,err=adapter.transform("status",{result={}},env)
  equal(value,nil);assert(err)
  e.routes={{dst="default",dev="br-lan",gateway="192.168.31.1"},
    {dst="default",dev="usb0",gateway="192.168.8.1"}}
  value,err=adapter.transform("status",{result={}},env)
  equal(value,nil);assert(err)
end)

test("higher-metric ambiguity does not hide a later unique preferred route",function()
  local e,env=fixture()
  e.routes={{dst="default",dev="usb0",gateway="192.168.8.1",metric=20},
    {dst="default",dev="eth1",gateway="192.168.9.1",metric=20},
    {dst="default",dev="br-lan",gateway="192.168.31.1",metric=0}}
  local value=assert(adapter.transform("status",{result={}},env))
  equal(value.result.defaultInterface,"lan")
end)

test("unsupported multipath backup does not block a valid lower-metric primary",function()
  local e,env=fixture()
  e.routes[2]={dst="default",metric=20,nexthops={{dev="usb0"},{dev="eth1"}}}
  local value=assert(adapter.transform("status",{result={}},env))
  equal(value.result.defaultInterface,"lan")
  e.routes[2].metric=0
  local bad,err=adapter.transform("status",{result={}},env)
  equal(bad,nil);assert(err)
end)

test("backend JSON errors are preserved without collection", function()
  local e,env=fixture()
  for _,original in ipairs({{error="denied",success=403},{success=501,result={}}, {error="oops"}}) do
    equal(adapter.transform("status",original,env),original)
  end
  equal(e.command_calls,0)
end)

test("first sample measures actual 100ms counter deltas", function()
  local e,env=fixture()
  local value,err=adapter.transform("statistics",{result={items={{startTime=1}},slots=12}},env)
  assert(value and not err)
  equal(e.slept,1);equal(e.locked,false)
  equal(#value.result.items,1);equal(value.result.slots,12)
  local item=value.result.items[1]
  assert(math.abs(item.downloadSpeed-1000)<0.001)
  assert(math.abs(item.uploadSpeed-2000)<0.001)
  assert(item.endTime>item.startTime)
  equal(value.result.dashboardAdapter.measurement,"interface-rx-tx")
  equal(e.state.items[1].downloadSpeed,item.downloadSpeed)
end)

test("subsequent samples reuse monotonic differences and cap real history at 12", function()
  local e,env=fixture()
  assert(adapter.transform("statistics",{result={}},env))
  for i=1,15 do
    e.time=e.time+5;e.wall=e.wall+5;e.rx=e.rx+500;e.tx=e.tx+1500
    local value=assert(adapter.transform("statistics",{result={}},env))
    equal(value.result.items[#value.result.items].downloadSpeed,100)
    equal(value.result.items[#value.result.items].uploadSpeed,300)
    equal(#value.result.items,math.min(i+1,12))
  end
  equal(e.slept,1)
end)

test("counter reset, interface generation, boot and route changes discard stale history", function()
  for _,change in ipairs({function(e)e.rx=1 end,function(e)e.ifindex=8 end,
    function(e)e.boot="new-boot" end,function(e)e.routes[1].gateway="192.168.31.254" end}) do
    local e,env=fixture()
    assert(adapter.transform("statistics",{result={}},env))
    e.time=e.time+5;e.wall=e.wall+5;change(e)
    local value=assert(adapter.transform("statistics",{result={}},env))
    equal(e.slept,2);equal(#value.result.items,1)
  end
end)

test("lock, invalid counters and atomic state errors do not fake successful samples", function()
  local e,env=fixture();e.locked=true
  local value,err=adapter.transform("statistics",{result={}},env)
  equal(value,nil);assert(err);equal(e.reads,0)
  e,env=fixture();e.rx=nil
  value,err=adapter.transform("statistics",{result={}},env)
  equal(value,nil);assert(err);equal(e.state,nil);equal(e.locked,false)
  e,env=fixture();env.store=function()return nil,"write failed" end
  value,err=adapter.transform("statistics",{result={}},env)
  equal(value,nil);assert(err);equal(e.locked,false)
end)

test("route switching during initial measurement returns retryable error",function()
  local e,env=fixture()
  local sleep=env.sleep
  env.sleep=function(s)sleep(s);e.routes[1].gateway="192.168.31.254" end
  local value,err=adapter.transform("statistics",{result={}},env)
  equal(value,nil);assert(err);equal(e.state,nil);equal(e.locked,false)
end)

print("passed " .. passed .. " dashboard adapter tests")
