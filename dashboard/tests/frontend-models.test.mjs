import {test} from 'node:test';
import assert from 'node:assert/strict';
for(const suite of ['history','portfolio','arrivals','profit','heat','artist','tracking_priority']){
  test(`${suite}: portable synthetic frontend assertions`,async()=>{
    const {acceptance}=await import(`../../tests/frontend/${suite}.mjs`);
    assert.equal(acceptance.passed,acceptance.total);
    assert.ok(acceptance.total>0);
  });
}
