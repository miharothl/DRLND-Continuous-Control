import numpy as np
from collections import namedtuple
import torch
import torch.nn.functional as F
import torch.optim as optim

from drl import drl_logger
from drl.agent.agent import Agent
from drl.agent.tools.ou_noise import OUNoise, OUNoiseStandardNormal
from drl.agent.tools.replay_buffer import ReplayBuffer
from drl.experiment.configuration import Configuration
from drl.model.model_factory import ModelFactory

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


class DdpgAgent(Agent):
    """Interacts with and learns from the environment."""

    def __init__(self, seed, cfg: Configuration, num_agents=1):
        super(DdpgAgent, self).__init__(cfg)

        """Initialize an Agent object.

        Params
        ======
            seed (int): random seed
            cfg (Config): configration
            num_agents(int): number of agents
        """

        self.num_agents = cfg.get_current_exp_cfg().environment_cfg.num_agents

        self.state_size = cfg.get_current_exp_cfg().agent_cfg.state_size
        self.action_size = cfg.get_current_exp_cfg().agent_cfg.action_size

        # Actor Network (w/ Target Network)
        # Critic Network (w/ Target Network)
        self.actor_local, self.actor_target, self.critic_local, self.critic_target = ModelFactory.create(seed, device, cfg)

        self.actor_optimizer = optim.Adam(
            self.actor_local.parameters(),
            lr=self.reinforcement_learning_cfg.ddpg_cfg.lr_actor)

        self.critic_optimizer = optim.Adam(
            self.critic_local.parameters(),
            lr=self.reinforcement_learning_cfg.ddpg_cfg.lr_critic,
            weight_decay=self.reinforcement_learning_cfg.ddpg_cfg.weight_decay)

        # Noise process
        self.noise = OUNoiseStandardNormal(self.action_size, seed)

        # Replay Memory
        if self.replay_memory_cfg.prioritized_replay:
            raise Exception('Prioritized replay is not supported.')
        else:
            self.memory = ReplayBuffer(self.replay_memory_cfg.buffer_size)
            self.beta_schedule = None

        # Initialize time step (for updating every UPDATE_EVERY steps)
        self.step_update_counter = 0

    def act(self, state, eps=0., add_noise=True):
        """Returns actions for given state as per current policy."""
        state = torch.from_numpy(state).float().to(device)
        self.actor_local.eval()
        with torch.no_grad():
            action = self.actor_local(state).cpu().data.numpy()
        self.actor_local.train()
        if add_noise:
            if self.num_agents == 1:
                action += self.noise.sample() * eps
            else:
                action += [self.noise.sample() * eps for _ in range(self.num_agents)]

        return np.clip(action, -1, 1)

    def get_models(self):

        model_actor = namedtuple('name', 'weights')
        model_actor.name = 'current_actor'
        model_actor.weights = self.actor_local

        model_critic = namedtuple('name', 'weights')
        model_critic.name = 'current_critic'
        model_critic.weights = self.critic_local

        return [model_actor, model_critic]

    def learn(self, experiences, gamma):
        """Update policy and value parameters using given batch of experience tuples.
        Q_targets = r + γ * critic_target(next_state, actor_target(next_state))
        where:
            actor_target(state) -> action
            critic_target(state, action) -> Q-value

        Params
        ======
            experiences (Tuple[torch.Tensor]): tuple of (s, a, r, s', done) tuples 
            gamma (float): discount factor
        """
        states, actions, rewards, next_states, dones, weights = experiences

        states = torch.from_numpy(states).float().to(device)
        actions = torch.from_numpy(actions).float().to(device)
        rewards = torch.from_numpy(rewards).float().unsqueeze(1).to(device)
        next_states = torch.from_numpy(next_states).float().to(device)
        dones = torch.from_numpy(dones.astype(np.uint8)).float().unsqueeze(1).to(device)
        weights = torch.from_numpy(weights).float().unsqueeze(1).to(device)

        # ---------------------------- update critic ---------------------------- #
        # Get predicted next-state actions and Q values from target models
        actions_next = self.actor_target(next_states)
        Q_targets_next = self.critic_target(next_states, actions_next)
        # Compute Q targets for current states (y_i)
        Q_targets = rewards + (gamma * Q_targets_next * (1 - dones))
        # Compute critic loss
        Q_expected = self.critic_local(states, actions)
        critic_loss = F.mse_loss(Q_expected, Q_targets)
        # Minimize the loss
        self.critic_optimizer.zero_grad()
        critic_loss.backward()
        self.critic_optimizer.step()

        # ---------------------------- update actor ---------------------------- #
        # Compute actor loss
        actions_pred = self.actor_local(states)
        actor_loss = -self.critic_local(states, actions_pred).mean()
        # Minimize the loss
        self.actor_optimizer.zero_grad()
        actor_loss.backward()
        self.actor_optimizer.step()

        # ----------------------- update target networks ----------------------- #
        self.soft_update(self.critic_local, self.critic_target, self.trainer_cfg.tau)
        self.soft_update(self.actor_local, self.actor_target, self.trainer_cfg.tau)

        return 0, 0, 0, 0

    def pre_process(self, state):
        return state

    def reset(self):
        self.noise.reset()

    def step(self, state, action, reward, next_state, done):
        """Save experience in replay memory, and use random sample from buffer to learn."""
        # Save experience / reward

        if self.num_agents == 1:
            self.memory.add(state, action, reward, next_state, done)
        else:
            for i in range(self.num_agents):
                self.memory.add(state[i], action[i], reward[i], next_state[i], done[i])

        # Learn every UPDATE_EVERY time steps.
        self.step_update_counter = (self.step_update_counter + 1) % self.trainer_cfg.update_every
        if self.step_update_counter == 0:

            # Learn, if enough samples are available in memory
            if len(self.memory) > self.trainer_cfg.batch_size:

                for _ in range(self.trainer_cfg.num_updates):

                    if self.replay_memory_cfg.prioritized_replay:
                        raise Exception('Prioritized replay is not supported.')
                    else:
                        experiences = self.memory.sample(self.trainer_cfg.batch_size)

                        obses_t, actions, rewards, obses_tp1, dones = experiences
                        weights, batch_idxes = np.ones_like(rewards), None
                        exp = (obses_t, actions, rewards, obses_tp1, dones, weights)

                    pos_reward_ratio, neg_reward_ratio, loss, td_error = self.learn(exp, self.trainer_cfg.gamma)

                    if self.replay_memory_cfg.prioritized_replay:
                        raise Exception('Prioritized replay is not supported.')

        return 0, 0, 0, 0
