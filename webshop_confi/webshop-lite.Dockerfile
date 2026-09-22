# webshop-lite.Dockerfile
FROM longinyu/agentbench-webshop

WORKDIR /root/webshop

RUN pip install --no-cache-dir ijson

COPY filter_goals.py filter_goals.py
RUN python filter_goals.py

RUN sed -i \
    -e "s|items_ins_v2\.json|items_ins_v2_goals.json|" \
    -e "s|items_shuffle\.json|items_shuffle_goals.json|" \
    web_agent_site/utils.py && \
    grep -n "DEFAULT_.*PATH" web_agent_site/utils.py

WORKDIR /root/webshop/search_engine
RUN rm -rf indexes resources resources_100 resources_1k resources_100k && \
    mkdir -p resources resources_100 resources_1k resources_100k indexes && \
    python convert_product_file_format.py && \
    ./run_indexing.sh

WORKDIR /root/webshop