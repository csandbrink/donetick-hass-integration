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
const members=[{user_id:1,display_name:'Christoph'},{user_id:2,display_name:'Iduna'},{user_id:3,display_name:'Jakob'},{user_id:4,display_name:'Jana'}];
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
assert.deepEqual(choices().map(button=>button.textContent),['CChristoph','IIduna','JaJakob','JaJana']);
assert.deepEqual(choices().map(button=>button.title),['Completed by Christoph','Completed by Iduna','Completed by Jakob','Completed by Jana']);
assert.equal(choices()[1].title,'Completed by Iduna');
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
assert.deepEqual(choices().map(button=>button.textContent),['CChristoph','IIduna','JaJakob','JaJana']);
// The ranking reaches the panel: this task's own people come before the circle's order.
circles()[1].click();circles()[2].click();
assert.deepEqual(choices().map(button=>button.textContent),['IIduna','JaJakob','CChristoph','JaJana']);
assert.equal(choices()[0].getAttribute('aria-label'),'Completed by Iduna');
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

// Switching the option off closes an open panel.
card.setConfig({...base});
circles()[0].click();
assert.ok(root.querySelector('.panel-members'));
card.setConfig({...base,credit_completion:'assignee'});
assert.equal(root.querySelector('.action-panel'),null);
console.log('Turning the option off closes a panel that is still open');
