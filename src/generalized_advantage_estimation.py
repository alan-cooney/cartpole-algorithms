import torch
import torch.nn as nn
from torch.distributions.categorical import Categorical
from torch.optim import Adam, Optimizer
import numpy as np
import gym


def create_model(number_observation_features: int, number_outputs: int) -> nn.Module:
    """Create a simple MLP model

    Args:
        number_observation_features (int): Number of features in the (flat)
        observation tensor
        number_actions (int): Number of outputs (actions in the case of the
        policy function, or 1 in the case of the value function)

    Returns:
        nn.Module: Simple MLP model
    """
    hidden_layer_features = 32

    return nn.Sequential(
        nn.Linear(in_features=number_observation_features,
                  out_features=hidden_layer_features),
        nn.ReLU(),
        nn.Linear(in_features=hidden_layer_features,
                  out_features=number_outputs),
    )


def get_policy(model: nn.Module, observation: np.ndarray) -> Categorical:
    """Get the policy from the model, for a specific observation

    Args:
        model (nn.Module): MLP model
        observation (np.ndarray): Environment observation

    Returns:
        Categorical: Multinomial distribution parameterized by model logits
    """
    observation_tensor = torch.as_tensor(observation)
    logits = model(observation_tensor)

    # Categorical will also normalize the logits for us
    return Categorical(logits=logits)


def get_action(policy: Categorical) -> tuple[int, float]:
    """Sample an action from the policy

    Args:
        policy (Categorical): Policy

    Returns:
        tuple[int, float]: Tuple of the action and it's log probability
    """
    action = policy.sample()  # Unit tensor

    # Converts to an int, as this is what Gym environments require
    action_int = action.item()

    # Calculate the log probability of the action, which is required for
    # calculating the loss later
    log_probability_action = policy.log_prob(action)

    return action_int, log_probability_action


def calculate_value_fn_loss(
        epoch_action_returns: list[int],
        epoch_state_value_estimates: list[torch.Tensor]) -> float:
    """Calculate the value function (critic) loss

    Uses mean squared error

    Args:
        epoch_action_returns (list[int]): State-action returns
        epoch_state_value_estimates (list[torch.Tensor]): State value estimates

    Returns:
        float: Mean square error (MSE)
    """
    value_estimates = torch.stack(epoch_state_value_estimates)
    returns = torch.as_tensor(epoch_action_returns)
    return ((value_estimates - returns)**2).mean()


def calculate_policy_fn_loss(
        epoch_log_probability_actions: torch.Tensor,
        epoch_action_rewards: torch.Tensor) -> float:
    """Calculate the 'loss' required to get the policy gradient

    Formula for gradient at
    https://spinningup.openai.com/en/latest/spinningup/rl_intro3.html#deriving-the-simplest-policy-gradient

    Note that this isn't really loss - it's just the sum of the log probability
    of each action times the episode return. We calculate this so we can
    back-propagate to get the policy gradient.

    Args:
        epoch_log_probability_actions (torch.Tensor): Log probabilities of the
            actions taken
        epoch_action_rewards (torch.Tensor): Rewards for each of these actions

    Returns:
        float: Pseudo-loss
    """
    return -(epoch_log_probability_actions * epoch_action_rewards).mean()


def run_one_episode(
    env: gym.Env,
    policy_function_model: nn.Module,
    value_function_model: nn.Module
) -> tuple[list[int], list[torch.Tensor], list[torch.Tensor], int]:
    """Run one episode

    Args:
        env (gym.Env): Gym environment
        policy_function_model (nn.Module): Policy function model (actor)
        value_function_model (nn.Module): Value function model (critic)
        optimizer (Optimizer): Optimizer

    Returns:
        tuple[list[int], list[float], int]: Tuple of action returns, log
        probabilities of those actions, state value estimates, and the total episode return
    """
    # Keep track of metrics
    rewards: list[int] = []
    state_value_estimates: list[torch.Tensor] = []
    log_probability_actions: list[torch.Tensor] = []

    # Reset the environment and get a fresh observation
    observation = env.reset()

    # Loop through timesteps (until done)
    while True:
        # Estimate the value of the state
        state_value_estimate = value_function_model(
            torch.as_tensor(observation))
        state_value_estimates.append(state_value_estimate)

        # Get the policy and act
        policy = get_policy(policy_function_model, observation)
        action, log_probability_action = get_action(policy)
        observation, reward, done, _ = env.step(action)

        # Update the metrics
        rewards.append(reward)
        log_probability_actions.append(log_probability_action)

        # Finish the action loop if this episode is done
        if done == True:
            break

    # Calculate the action returns
    episode_return = sum(rewards)
    total_timesteps = len(rewards)
    action_returns = [episode_return] * total_timesteps

    return action_returns, log_probability_actions, state_value_estimates, episode_return


def train_one_epoch(
    env: gym.Env,
    policy_function_model: nn.Module,
    policy_function_optimizer: Optimizer,
    value_function_model: nn.Module,
    value_function_optimizer: Optimizer
) -> float:
    """Train one epoch

    Args:
        env (gym.Env): Gym environment
        policy_function_model (nn.Module): Policy function model (actor)
        policy_function_optimizer (Optimizer): Policy function optimizer (actor)

    Returns:
        float: Average return from the epoch
    """
    # Keep track of metrics
    epoch_action_returns: list[int] = []
    epoch_log_probability_actions: list[torch.Tensor] = []
    epoch_state_value_estimates: list[torch.Tensor] = []
    epoch_episode_returns: list[int] = []

    # Run a batch of episodes
    for _ in range(200):
        action_returns, log_probability_actions, state_value_estimates, episode_return = run_one_episode(
            env,
            policy_function_model,
            value_function_model)
        epoch_action_returns += action_returns
        epoch_log_probability_actions += log_probability_actions
        epoch_state_value_estimates += state_value_estimates
        epoch_episode_returns.append(episode_return)

    # Calculate the policy function (actor) loss
    policy_function_loss = calculate_policy_fn_loss(
        torch.stack(epoch_log_probability_actions),
        torch.as_tensor(epoch_action_returns)
    )

    # Calculate the value function (critic) loss
    value_function_loss = calculate_value_fn_loss(
        epoch_action_returns, epoch_state_value_estimates)

    # Step the weights and biases
    value_function_loss.backward()
    value_function_optimizer.step()

    policy_function_loss.backward()
    policy_function_optimizer.step()

    value_function_optimizer.zero_grad()
    policy_function_optimizer.zero_grad()

    return np.mean(epoch_episode_returns)


def train(epochs=30) -> None:
    """Train a Vanilla Policy Gradient model on CartPole

    Args:
        episodes (int, optional): The number of episodes to run for. Defaults to 2000.
    """

    # Create the Gym Environment
    env = gym.make('CartPole-v0')

    # Use random seeds (to make experiments deterministic)
    torch.manual_seed(0)
    env.seed(0)

    # Create the MLP models
    # Note we create 2 separate models - one for the policy function and one for
    # the value function (i.e. actor and critic). It is common to instead create
    # 2 models with some shared layers for this purpose (as the lower layers are
    # learning similar things), but for simplicity here they're kept entirely
    # separate.
    number_observation_features = env.observation_space.shape[0]
    number_actions = env.action_space.n
    policy_function_model = create_model(
        number_observation_features, number_actions)
    value_function_model = create_model(number_observation_features, 1)

    # Create the optimizers
    policy_function_optimizer = Adam(policy_function_model.parameters(), 1e-2)
    value_function_optimizer = Adam(value_function_model.parameters(), 1e-2)

    # Loop for each epoch
    for epoch in range(epochs):
        average_return = train_one_epoch(
            env,
            policy_function_model,
            policy_function_optimizer,
            value_function_model,
            value_function_optimizer)

        print('epoch: %3d \t return: %.3f' % (epoch, average_return))


if __name__ == '__main__':
    train()