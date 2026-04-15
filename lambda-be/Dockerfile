FROM public.ecr.aws/lambda/python:3.11

RUN yum install -y mesa-libGL && yum clean all

RUN pip install --upgrade pip setuptools wheel

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY src/ ${LAMBDA_TASK_ROOT}/src/
COPY src/lambda_function.py ${LAMBDA_TASK_ROOT}/lambda_function.py

CMD [ "lambda_function.lambda_handler" ]
