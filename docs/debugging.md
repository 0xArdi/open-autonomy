## Debugging using Tenderly:

[Tenderly](https://tenderly.co/) is a *"comprehensive Ethereum Developer Platform for real-time monitoring, alerting, debugging, and simulating Smart Contracts"*. When debugging transactions and contract calls, it can be useful to help us understand what's going on with our execution. [This guide](http://blog.tenderly.co/level-up-your-smart-contract-productivity-using-hardhat-and-tenderly/) contains a more exhaustive explanation on Tenderly, but for the basics:

- Create a Tenderly account and project.

- Set your Tenderly username and project name at `tenderly.yaml` (`exports/ganache/project_slug`) and at your `hardhat.config.js` module exports:

    ```
    tenderly: {
      username: "username",
      project: "projectname"
    }
    ```

- Login to Tenderly using your username and password or an access token:
    ```
    tenderly login
    ```

- Run your Hardhat deployment and export your transactions with:
    ```
    tenderly export <transaction_hash>
    ```
- You'll see a link to your Tenderly dashboard where you can inspect the full transaction stack trace.
- During testing, you will need to pause Hardhat's execution before it ends to export the transaction.
- Optionally, there's the possibility of pushing your contract's source to verify and debug it. More on that [here](http://blog.tenderly.co/level-up-your-smart-contract-productivity-using-hardhat-and-tenderly/).