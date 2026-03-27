import os


def _normalized_repo_path(instance_repo_path):
    return (instance_repo_path or "").lower()


def _has_node_modules(instance_repo_path):
    if not instance_repo_path:
        return False
    return os.path.isdir(os.path.join(instance_repo_path, "node_modules"))

def make_check_cmd(instance_repo_path):
    if _has_node_modules(instance_repo_path):
        return 'ls'

    normalized_repo_path = _normalized_repo_path(instance_repo_path)
    if 'bpmn-js' in instance_repo_path:
        # build_cmd = 'pnpm install & pnpm run distro'
        build_cmd = 'ls'
    elif 'GoogleChrome__lighthouse' in instance_repo_path:
        # must use npm install
        build_cmd = 'ls'
    elif 'scratch-gui' in instance_repo_path:
        # must use npm install, you need use npm start to open browser
        # build_cmd = 'export NODE_OPTIONS=--openssl-legacy-provider && npm start'
        build_cmd = 'ls'
    elif 'openlayers' in instance_repo_path:
        # build_cmd = 'export NODE_OPTIONS=--openssl-legacy-provider && pnpm run build-legacy'
        build_cmd = 'ls'
    elif 'alibaba-fusion' in instance_repo_path:
        # must use old version node, such as "fnm use 14"
        build_cmd = 'ls'


    elif 'carbon-design-system' in instance_repo_path:
        # must use yarn install, then yarn add file:../carbon/packages/react in you web project
        # build_cmd = 'yarn build'

        # check errors
        build_cmd = 'yarn run lint'
    elif 'grommet' in instance_repo_path:
        # must use yarn install, then yarn add file:../carbon/packages/react in you web project
        build_cmd = 'yarn install && export NODE_OPTIONS=--openssl-legacy-provider && yarn run lint'
        # build_cmd = 'ls'
    elif 'prettier' in instance_repo_path:
        # must use yarn install
        build_cmd = 'yarn run lint'
        
    elif 'prismjs' in normalized_repo_path:
        build_cmd = 'ls'

    elif 'highlightjs' in normalized_repo_path:
        build_cmd = 'ls'

    else:
        build_cmd = 'ls'

    return build_cmd

def make_build_cmd(instance_repo_path):
    if _has_node_modules(instance_repo_path):
        return 'ls'

    normalized_repo_path = _normalized_repo_path(instance_repo_path)
    if 'bpmn-js' in instance_repo_path:
        # build_cmd = 'pnpm install & pnpm run distro'
        build_cmd = 'ls'
    elif 'GoogleChrome__lighthouse' in instance_repo_path:
        # must use npm install
        # yarn
        # yarn build-all
        build_cmd = 'ls'
    elif 'scratch-gui' in instance_repo_path:
        # must use npm install, you need use npm start to open browser
        build_cmd = 'export NODE_OPTIONS=--openssl-legacy-provider && npm start'
    elif 'openlayers' in instance_repo_path:
        build_cmd = 'export NODE_OPTIONS=--openssl-legacy-provider && pnpm run build-legacy'
    elif 'alibaba-fusion' in instance_repo_path:
        # must use old version node, such as "fnm use 14/12/10"
        # npm install node-sass@4.14.1 --save-dev
        build_cmd = 'ls'

        if 'alibaba-fusion__next-4859' in instance_repo_path:
            build_cmd = 'fnm use 14 && npm run build:dist'
        if 'alibaba-fusion__next-4182' in instance_repo_path:
            build_cmd = 'fnm use 14 && npm run build && npm run pack'
        if 'alibaba-fusion__next-2984' in instance_repo_path:
            build_cmd = 'fnm use 12 && npm run build && npm run pack'
        if 'alibaba-fusion__next-2860' in instance_repo_path:
            build_cmd = 'fnm use 10 && npm run build && npm run pack'
        if 'alibaba-fusion__next-2131' in instance_repo_path:
            build_cmd = 'fnm use 10 && npm run build && npm run pack'
        if 'alibaba-fusion__next-1708' in instance_repo_path:
            build_cmd = 'fnm use 10 && npm run build && npm run pack'
        if 'alibaba-fusion__next-1500' in instance_repo_path:
            build_cmd = 'fnm use 10 && npm run build && npm run pack'
        if 'alibaba-fusion__next-1509' in instance_repo_path:
            build_cmd = 'fnm use 10 && npm run build && npm run pack'
        if 'alibaba-fusion__next-966' in instance_repo_path:
            build_cmd = 'fnm use 10 && npm run build && npm run pack'
        if 'alibaba-fusion__next-877' in instance_repo_path:
            build_cmd = 'fnm use 10 && npm run build && npm run pack'
        if 'alibaba-fusion__next-895' in instance_repo_path:
            build_cmd = 'fnm use 10 && npm run build && npm run pack'
        if 'alibaba-fusion__next-717' in instance_repo_path:
            build_cmd = 'fnm use 10 && npm run build && npm run pack'
        if 'alibaba-fusion__next-94' in instance_repo_path:
            build_cmd = 'fnm use 10 && npm run build && npm run pack'

        # build_cmd = 'export NODE_OPTIONS=--openssl-legacy-provider && pnpm run pack'
    elif 'carbon-design-system' in instance_repo_path:
        # Note that different repo version need different Node version, use "fnm install xx", then "fnm use xx" to change the Node version
        # "npm install -g yarn", must use "yarn install", "yarn build", then "cd react/packages" run "yarn storybook" to open the dev demo
        
        # Note that carbon only run build one time, then we only need to update the code
        build_cmd = 'fnm use 22 && yarn build'

        if 'carbon-design-system__carbon-3118' in instance_repo_path:
            # build_cmd = 'fnm use 10 && yarn build'
            build_cmd = 'ls'
        if 'carbon-design-system__carbon-3139' in instance_repo_path:
            # build_cmd = 'fnm use 10 && yarn build'
            build_cmd = 'ls'
        if 'carbon-design-system__carbon-4167' in instance_repo_path:
            build_cmd = 'fnm use 10 && yarn build'
        if 'carbon-design-system__carbon-4347' in instance_repo_path:
            build_cmd = 'fnm use 10 && yarn build'
        if 'carbon-design-system__carbon-4816' in instance_repo_path:
            build_cmd = 'fnm use 10 && yarn build'
        if 'carbon-design-system__carbon-5156' in instance_repo_path:
            build_cmd = 'fnm use 10 && yarn build'
        if 'carbon-design-system__carbon-6906' in instance_repo_path:
            build_cmd = 'fnm use 12 && yarn build'
        if 'carbon-design-system__carbon-6964' in instance_repo_path:
            build_cmd = 'fnm use 12 && yarn build'
        if 'carbon-design-system__carbon-11664' in instance_repo_path:
            # build_cmd = 'fnm use 16 && yarn build'
            build_cmd = 'ls'
        if 'carbon-design-system__carbon-12332' in instance_repo_path:
            build_cmd = 'fnm use 16 && yarn build'
            # build_cmd = 'ls'

        if 'carbon-design-system__carbon-15197' in instance_repo_path:
            # build_cmd = 'fnm use 16 && yarn build'
            build_cmd = 'ls'
        if 'carbon-design-system__carbon-16237' in instance_repo_path:
            # build_cmd = 'fnm use 18 && yarn build'
            build_cmd = 'ls'
        # check errors
        if 'carbon-design-system__carbon-9136' in instance_repo_path:
            build_cmd = 'ls'
        

        # build_cmd = 'ls'
    elif 'grommet' in instance_repo_path:
        # must use yarn install, then yarn add file:../carbon/packages/react in you web project
        # build_cmd = 'yarn install && export NODE_OPTIONS=--openssl-legacy-provider && yarn run build'
        build_cmd = 'ls'
    elif 'prettier' in instance_repo_path:
        # must use yarn install, also can use "yarn run lint" to check code format 
        build_cmd = 'yarn run build'
        
        if 'prettier__prettier-11884' in instance_repo_path:
            build_cmd = 'fnm use 22 && NODE_OPTIONS=--openssl-legacy-provider yarn run build'
        if 'prettier__prettier-9866' in instance_repo_path:
            build_cmd = 'fnm use 22 && NODE_OPTIONS=--openssl-legacy-provider yarn run build'
        if 'prettier__prettier-8536' in instance_repo_path:
            build_cmd = 'fnm use 22 && NODE_OPTIONS=--openssl-legacy-provider yarn run build'
        if 'prettier__prettier-6319' in instance_repo_path:
            build_cmd = 'fnm use 22 && NODE_OPTIONS=--openssl-legacy-provider yarn run build'
        if 'prettier__prettier-4202' in instance_repo_path:
            build_cmd = 'fnm use 22 && NODE_OPTIONS=--openssl-legacy-provider npm run build'



    elif 'prismjs' in normalized_repo_path:
        # must use npm install
        build_cmd = 'ls'

    elif 'highlightjs' in normalized_repo_path:
        build_cmd = 'npm install && npm run build-browser'

    elif 'chartjs' in normalized_repo_path:
        build_cmd = 'pnpm install && pnpm build'

    elif 'markedjs' in normalized_repo_path:
        build_cmd = 'npm install && npm run build'
        
    else:
        build_cmd = 'ls'

    return build_cmd
