import assert from 'node:assert/strict';
const {Window} = await import(process.env.DOM_TEST_MODULE || 'happy-dom');
const window = new Window();
Object.assign(globalThis, {window, document: window.document, HTMLElement: window.HTMLElement,
  customElements: window.customElements, CustomEvent: window.CustomEvent});
customElements.define('ha-form',class extends HTMLElement {});
customElements.define('home-assistant',class extends HTMLElement {});
const {performerChoices} = await import('../custom_components/donetick/frontend/donetick-user-todo-card.js');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const calls=[]; let fail=false;
const members=[{user_id:1,display_name:'Casey'},{user_id:2,display_name:'Ivy'},{user_id:3,display_name:'Jamie'},{user_id:4,display_name:'Jasmin'}];
const hass={locale:{language:'en'},states:{'todo.all':{state:'3',attributes:{config_entry_id:'entry',circle_members:members,tasks:[
  {task_id:1,assigned_to:null,assignee_ids:[],name:'Shared',can_postpone:false},
  {task_id:2,assigned_to:1,assignee_ids:[1],name:'Mine',can_postpone:false},
  {task_id:3,assigned_to:2,assignee_ids:[2,3],name:'Rotating',can_postpone:false}
]}}},callService:async(...args)=>{calls.push(args);if(fail)throw new Error('Donetick task action failed: Donetick denied the completion (400): User is not assigned to chore');}};
const card=document.createElement('donetick-user-todo-card');
const base={entity:'todo.all',user_id:'all',complete_control:'initials',credit_completion:'choose'};
card.setConfig(base);card.hass=hass;document.body.append(card);
const root=card.shadowRoot;
const circles=()=>[...root.querySelectorAll('.complete-initials')];
const choices=()=>[...root.querySelectorAll('.panel-members button')];

// Everyone is offered; the task's own people come first and the server arbitrates.
const state=hass.states['todo.all'];
assert.deepEqual(performerChoices(state,state.attributes.tasks[0]).map(m=>m.user_id),[1,2,3,4]);
assert.deepEqual(performerChoices(state,state.attributes.tasks[1]).map(m=>m.user_id),[1,2,3,4]);
assert.deepEqual(performerChoices(state,state.attributes.tasks[2]).map(m=>m.user_id),[2,3,1,4]);
// An assignee who has left the circle cannot be offered, but nobody else disappears.
assert.deepEqual(performerChoices(state,{task_id:9,assigned_to:7,assignee_ids:[]}).map(m=>m.user_id),[1,2,3,4]);
assert.deepEqual(performerChoices({attributes:{circle_members:[]}},{task_id:9}),[]);
assert.deepEqual(performerChoices(undefined,{task_id:9}),[]);
console.log('Every circle member is offered, the task\'s own people first');

// Tapping asks instead of completing.
assert.equal(root.querySelector('.action-panel'),null);
circles()[0].click();
assert.equal(calls.length,0);
const form=root.querySelector('.action-panel');
assert.ok(form);
assert.equal(form.getAttribute('aria-label'),'Completed by: Shared');
assert.equal(root.querySelector('.action-panel .skip-prompt').textContent,'Completed by');
assert.equal(root.querySelector('.action-panel .save-action'),null);
assert.equal(circles()[0].getAttribute('aria-expanded'),'true');
assert.equal(circles()[1].getAttribute('aria-expanded'),'false');
assert.deepEqual(choices().map(button=>button.textContent),['CCasey','IIvy','JaJamie','JaJasmin']);
assert.deepEqual(choices().map(button=>button.title),['Completed by Casey','Completed by Ivy','Completed by Jamie','Completed by Jasmin']);
assert.equal(choices()[1].title,'Completed by Ivy');
console.log('The completion control opens a member row instead of booking the assignee');

// Picking a member books the completion for that member.
choices()[1].click();
assert.deepEqual(calls.pop(),['donetick','complete_assigned_task',{task_id:1,config_entry_id:'entry',completed_by:2}]);
await tick();
assert.equal(root.querySelector('.action-panel'),null);
assert.equal(root.querySelectorAll('.completed .complete-initials').length,1);
await new Promise(resolve=>setTimeout(resolve,1050));
console.log('Picking a member credits the completion to them and closes the panel');

// An assigned task offers only who the server would accept; failures stay visible.
circles()[1].click();
// The assignee leads, everyone else stays reachable behind them.
assert.deepEqual(choices().map(button=>button.textContent),['CCasey','IIvy','JaJamie','JaJasmin']);
// The ranking reaches the panel: this task's own people come before the circle's order.
circles()[1].click();circles()[2].click();
assert.deepEqual(choices().map(button=>button.textContent),['IIvy','JaJamie','CCasey','JaJasmin']);
assert.equal(choices()[0].getAttribute('aria-label'),'Completed by Ivy');
circles()[2].click();circles()[1].click();
fail=true;choices()[0].click();await tick();
assert.match(root.querySelector('.error').textContent,/denied the completion \(400\): User is not assigned to chore/);
assert.ok(root.querySelector('.action-panel'));
fail=false;
root.querySelector('.action-panel').dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
assert.equal(root.querySelector('.action-panel'),null);
console.log('A rejected completion keeps the panel open with the server error');

// The option is off by default and works with every completion control.
card.setConfig({entity:'todo.all',user_id:'all',complete_control:'initials'});
circles()[0].click();
assert.deepEqual(calls.pop(),['donetick','complete_assigned_task',{task_id:1,config_entry_id:'entry'}]);
assert.equal(root.querySelector('.action-panel'),null);
await new Promise(resolve=>setTimeout(resolve,1050));
assert.throws(()=>card.setConfig({...base,credit_completion:'ask'}),/valid completion credit/);
card.setConfig({...base,complete_control:'checkbox'});
const checkbox=root.querySelector('input[type=checkbox]');
calls.length=0;
checkbox.checked=true;checkbox.dispatchEvent(new window.Event('change'));
assert.equal(calls.length,0);
assert.ok(root.querySelector('.panel-members'));
// Tapping the control again closes the panel, as the other row panels do.
root.querySelector('input[type=checkbox]').dispatchEvent(new window.Event('change'));
assert.equal(root.querySelector('.action-panel'),null);
card.setConfig({...base,complete_control:'button',button_content:'icon_and_label'});
root.querySelector('[aria-label="Complete: Shared"]').click();
assert.ok(root.querySelector('.panel-members'));
assert.equal(root.querySelector('[aria-label="Complete: Shared"]').getAttribute('aria-expanded'),'true');
card.setConfig({...base,credit_completion:'choose',show_complete:false});
assert.equal(root.querySelector('.action-panel'),null);
console.log('Credit selection is opt-in and available for every completion control');

// An empty circle list must not make a task uncompletable.
const bare=document.createElement('donetick-user-todo-card');
const bareHass={...hass,states:{'todo.all':{state:'1',attributes:{config_entry_id:'entry',circle_members:[],
  tasks:[{task_id:9,assigned_to:null,assignee_ids:[],name:'Alone',can_postpone:false}]}}}};
bare.setConfig({entity:'todo.all',user_id:'all',complete_control:'initials',credit_completion:'choose'});
bare.hass=bareHass;document.body.append(bare);
calls.length=0;
bare.shadowRoot.querySelector('.complete-initials').click();
assert.equal(bare.shadowRoot.querySelector('.action-panel'),null);
assert.deepEqual(calls.pop(),['donetick','complete_assigned_task',{task_id:9,config_entry_id:'entry'}]);
console.log('Without a circle list the control completes instead of opening an empty panel');
await new Promise(resolve=>setTimeout(resolve,1050));

// Switching the option off closes an open panel.
card.setConfig({...base});
circles()[0].click();
assert.ok(root.querySelector('.panel-members'));
card.setConfig({...base,credit_completion:'assignee'});
assert.equal(root.querySelector('.action-panel'),null);
console.log('Turning the option off closes a panel that is still open');

// A slow call must leave the rest of the card alone, and its refusal must stay its own.
let accept, refuse;
const slowHass={locale:{language:'en'},states:{'todo.all':{state:'2',attributes:{config_entry_id:'entry',circle_members:members,tasks:[
  {task_id:1,assigned_to:1,assignee_ids:[1],name:'Dishes',can_postpone:false},
  {task_id:2,assigned_to:2,assignee_ids:[2],name:'Laundry',can_postpone:false}
]}}},callService:(...args)=>new Promise((resolve,reject)=>{calls.push(args);accept=resolve;refuse=()=>reject(new Error(
  'Donetick task action failed: Donetick denied the completion (400): User is not assigned to chore'));})};
const slow=document.createElement('donetick-user-todo-card');
slow.setConfig(base);slow.hass=slowHass;document.body.append(slow);
const slowRoot=slow.shadowRoot;
const slowCircles=()=>[...slowRoot.querySelectorAll('.complete-initials')];
const cardError=()=>[...slowRoot.querySelectorAll('ha-card > .error')].map(error=>error.textContent);
slowCircles()[0].click();
slowRoot.querySelector('.panel-members button').click();
// Everyone stays put while the booking is in flight, so a mistap cannot double-book.
assert.deepEqual([...slowRoot.querySelectorAll('.panel-members button')].map(button=>button.disabled),[true,true,true,true]);
slowCircles()[1].click();
assert.equal(slowRoot.querySelector('.action-panel').getAttribute('aria-label'),'Completed by: Laundry');
refuse();await tick();await tick();
// The second row's panel survives the first row's answer, and wears none of it.
assert.equal(slowRoot.querySelector('.action-panel').getAttribute('aria-label'),'Completed by: Laundry');
assert.equal(slowRoot.querySelector('.action-panel .error'),null);
assert.match(cardError()[0],/^Dishes: .*User is not assigned to chore$/);
assert.equal(slowRoot.querySelectorAll('[role=alert]').length,1);
console.log('A refusal names its own task and never lands on another row\'s panel');

// The same holds when the first row's booking succeeds: it closes its own panel only.
slow.setConfig(base);
calls.length=0;
slowCircles()[0].click();
slowRoot.querySelector('.panel-members button').click();
slowCircles()[1].click();
assert.equal(slowRoot.querySelector('.action-panel').getAttribute('aria-label'),'Completed by: Laundry');
accept();await tick();await tick();
assert.deepEqual(calls.pop(),['donetick','complete_assigned_task',{task_id:1,config_entry_id:'entry',completed_by:1}]);
assert.equal(slowRoot.querySelector('.action-panel').getAttribute('aria-label'),'Completed by: Laundry');
assert.equal(cardError().length,0);
console.log('A booking that succeeds closes its own panel and leaves another row\'s open');
await new Promise(resolve=>setTimeout(resolve,1050));

// The panel closes rather than standing empty if the circle list empties under it.
slow.setConfig(base);
slowCircles()[0].click();
assert.ok(slowRoot.querySelector('.panel-members'));
slowHass.states['todo.all']={...slowHass.states['todo.all'],attributes:{...slowHass.states['todo.all'].attributes,circle_members:[]}};
slow.hass=slowHass;
assert.equal(slowRoot.querySelector('.action-panel'),null);
assert.equal(slowRoot.querySelector('.panel-members'),null);
console.log('A circle list that empties under an open panel closes it instead of showing nothing');
